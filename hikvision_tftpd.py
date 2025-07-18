#!/usr/bin/env python3
"""
Unbrick a Hikvision device. See README.md for usage information.
"""

__author__ = 'Scott Lamb'
__license__ = 'MIT'
__email__ = 'slamb@slamb.org'

import argparse
import errno
import select
import socket
import struct
import sys
import time
from typing import Dict, Tuple

HANDSHAKE_BYTES = struct.pack('20s', b'SWKH')
_HANDSHAKE_SERVER_PORT = 9978
_TFTP_SERVER_PORT = 69
_TIME_FMT = '%c'
_DEFAULT_BLOCK_SIZE = 512


class Error(Exception):
    pass


class Server:
    # See https://tools.ietf.org/html/rfc1350
    _TFTP_OPCODE_RRQ = 1
    _TFTP_OPCODE_DATA = 3
    _TFTP_OPCODE_ACK = 4
    _TFTP_OPCODE_OACK = 6
    _TFTP_ACK_PREFIX = struct.pack('>h', _TFTP_OPCODE_ACK)

    def __init__(
        self,
        handshake_addr: Tuple[str, int],
        tftp_addr: Tuple[str, int],
        filename: str,
        file_contents: bytes,
    ) -> None:
        self._file_contents = file_contents
        self._filename = filename
        self._tftp_rrq_prefix = (
            struct.pack('>h', self._TFTP_OPCODE_RRQ)
            + filename.encode('utf-8')
            + b'\x00'
        )
        self._tftp_blksize_option = b'blksize\x00'
        self._handshake_sock = self._bind(handshake_addr)
        self._tftp_sock = self._bind(tftp_addr)
        self._set_block_size(_DEFAULT_BLOCK_SIZE)
        # Track active transfers for logging
        self._active_transfers: Dict[str, Dict[str, float]] = {}

    def _bind(self, addr: Tuple[str, int]) -> socket.socket:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.bind(addr)
        except OSError as e:
            if e.errno == errno.EADDRNOTAVAIL:
                raise Error(
                    f'Address {addr[0]}:{addr[1]} not available.\n\n'
                    f'Try running:\n'
                    f'linux$ sudo ifconfig eth0:0 {addr[0]}\n'
                    f'osx$   sudo ifconfig en0 alias {addr[0]} '
                    f'255.255.255.0\n\n'
                    f'(adjust eth0 or en0 to taste. see "ifconfig -a" output)'
                ) from e
            if e.errno == errno.EADDRINUSE:
                raise Error(
                    f'Address {addr[0]}:{addr[1]} in use.\n'
                    f'Make sure no other TFTP server is running.'
                ) from e
            if e.errno == errno.EACCES:
                raise Error(
                    f'No permission to bind to {addr[0]}:{addr[1]}.\n'
                    f'Try running with sudo.'
                ) from e
            raise
        return sock

    def _set_block_size(self, block_size: int) -> None:
        # TODO: Don't mutate overall server for a single transfer. Use some kind of per-transfer state
        print(f'Setting block size to {block_size}')
        self._block_size = block_size
        self._total_blocks = (
            len(self._file_contents) + self._block_size
        ) // self._block_size
        print(
            f'Serving {len(self._file_contents)}-byte {self._filename} (block size {self._block_size}, {self._total_blocks} blocks)'
        )

    def _check_total_block_limit(self) -> None:
        if self._total_blocks > 65535:
            raise Error(
                f'File is too big to serve with {self._block_size}-byte blocks.'
            )

    def _parse_options(self, pkt: bytes) -> Dict[str, str]:
        pkt_options = pkt.split(self._tftp_rrq_prefix)[1]
        options_list = pkt_options.split(b'\x00')[1:]
        options = {}
        for i in range(0, len(options_list) - 1, 2):
            options[options_list[i].decode('utf-8')] = options_list[i + 1].decode(
                'utf-8'
            )
        print(f'read request options: {options}')
        return options

    def close(self) -> None:
        self._handshake_sock.close()
        self._tftp_sock.close()

    def run_forever(self) -> None:
        while True:
            self._iterate()

    def _iterate(self) -> None:
        r, _, _ = select.select([self._handshake_sock, self._tftp_sock], [], [])
        if self._handshake_sock in r:
            self._handshake_read()
        if self._tftp_sock in r:
            self._tftp_read()

    def _handshake_read(self) -> None:
        pkt, addr = self._handshake_sock.recvfrom(len(HANDSHAKE_BYTES))
        now = time.strftime(_TIME_FMT)
        if pkt == HANDSHAKE_BYTES:
            try:
                self._handshake_sock.sendto(pkt, addr)
                print(
                    f'{now}: {addr[0]}:{addr[1]} - HANDSHAKE - "SWKH" 200 {len(HANDSHAKE_BYTES)}'
                )
            except OSError as e:
                print(
                    f'{now}: {addr[0]}:{addr[1]} - HANDSHAKE - "SWKH" 503 {len(HANDSHAKE_BYTES)} - network error: {e}'
                )
        else:
            print(
                f'{now}: {addr[0]}:{addr[1]} - HANDSHAKE - "INVALID" 400 {len(pkt)} - unexpected handshake bytes {pkt.hex()!r}'
            )

    def _tftp_read(self) -> None:
        pkt, addr = self._tftp_sock.recvfrom(65536)
        now = time.strftime(_TIME_FMT)
        client_key = f'{addr[0]}:{addr[1]}'

        if pkt.startswith(self._tftp_rrq_prefix):
            # Log the read request
            print(
                f'{now}: {addr[0]}:{addr[1]} - TFTP_RRQ - "GET {self._filename}" - {len(self._file_contents)} bytes'
            )

            # Track transfer start time
            self._active_transfers[client_key] = {
                'start_time': time.time(),
                'bytes_sent': 0,
                'blocks_sent': 0,
            }

            options = self._parse_options(pkt)
            if 'blksize' in options:
                self._set_block_size(int(options['blksize']))
                print(
                    f'{now}: {addr[0]}:{addr[1]} - TFTP_OACK - "blksize {self._block_size}" - negotiated block size'
                )
                self._tftp_options_ack(addr)
                return
            self._check_total_block_limit()
            self._tftp_maybe_send(0, addr)
        elif pkt.startswith(self._TFTP_ACK_PREFIX):
            (block,) = struct.unpack('>H', pkt[len(self._TFTP_ACK_PREFIX) :])
            self._tftp_maybe_send(block, addr)
        else:
            print(
                f'{now}: {addr[0]}:{addr[1]} - TFTP_ERROR - "INVALID" 400 {len(pkt)} - unexpected tftp bytes {pkt.hex()!r}'
            )

    def _tftp_options_ack(self, addr: Tuple[str, int]) -> None:
        self._check_total_block_limit()
        pkt = (
            struct.pack('>H', self._TFTP_OPCODE_OACK)
            + b'blksize\x00'
            + str(self._block_size).encode('utf-8')
            + b'\x00'
        )
        try:
            self._tftp_sock.sendto(pkt, addr)
        except OSError as e:
            now = time.strftime(_TIME_FMT)
            print(
                f'{now}: {addr[0]}:{addr[1]} - TFTP_OACK - "blksize {self._block_size}" 503 - network error: {e}'
            )

    def _tftp_maybe_send(self, prev_block: int, addr: Tuple[str, int]) -> None:
        block = prev_block + 1
        start_byte = prev_block * self._block_size
        client_key = f'{addr[0]}:{addr[1]}'
        now = time.strftime(_TIME_FMT)

        if start_byte > len(self._file_contents):
            # Transfer completed - log completion stats
            if client_key in self._active_transfers:
                transfer_info = self._active_transfers[client_key]
                duration = time.time() - transfer_info['start_time']
                print(
                    f'{now}: {addr[0]}:{addr[1]} - TFTP_COMPLETE - "GET {self._filename}" 200 {len(self._file_contents)} - {duration:.2f}s - {transfer_info["blocks_sent"]} blocks'
                )
                del self._active_transfers[client_key]
            else:
                print(
                    f'{now}: {addr[0]}:{addr[1]} - TFTP_COMPLETE - "GET {self._filename}" 200 {len(self._file_contents)} - transfer complete'
                )

            if self._block_size != _DEFAULT_BLOCK_SIZE:
                self._set_block_size(_DEFAULT_BLOCK_SIZE)
            return

        block_data = self._file_contents[start_byte : start_byte + self._block_size]
        pkt = struct.pack('>hH', self._TFTP_OPCODE_DATA, block) + block_data
        try:
            self._tftp_sock.sendto(pkt, addr)
        except OSError as e:
            print(
                f'{now}: {addr[0]}:{addr[1]} - TFTP_DATA - "block {block}" 503 - network error: {e}'
            )
            return

        # Update transfer tracking
        if client_key in self._active_transfers:
            self._active_transfers[client_key]['bytes_sent'] = start_byte + len(
                block_data
            )
            self._active_transfers[client_key]['blocks_sent'] = block

        # Log progress at 25%, 50%, 75% milestones
        progress_percent = (block * 100) // self._total_blocks
        if progress_percent in [25, 50, 75] and client_key in self._active_transfers:
            transfer_info = self._active_transfers[client_key]
            duration = time.time() - transfer_info['start_time']
            print(
                f'{now}: {addr[0]}:{addr[1]} - TFTP_PROGRESS - "{progress_percent}%" - {transfer_info["bytes_sent"]} bytes - {duration:.1f}s'
            )

        # Show block progress bar
        _progress_width = 53
        print(
            f'{now}: {block:5d} / {self._total_blocks:5d} [{"#" * (_progress_width * block // self._total_blocks):<{_progress_width}}]'
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--filename',
        default='digicap.dav',
        help='file to serve; used both to read from the local '
        'disk and for the filename to expect from client',
    )
    parser.add_argument(
        '--server-ip', default='192.0.0.128', help='IP address to serve from.'
    )
    args = parser.parse_args()
    try:
        with open(args.filename, mode='rb') as f:
            file_contents = f.read()
    except OSError as e:
        print(f"Error: can't read {args.filename}")
        if e.errno == errno.ENOENT:
            print('Please download/move it to the current working directory.')
            sys.exit(1)
        raise

    try:
        server = Server(
            (args.server_ip, _HANDSHAKE_SERVER_PORT),
            (args.server_ip, _TFTP_SERVER_PORT),
            args.filename,
            file_contents,
        )
    except Error as e:
        print(f'Error: {e}')
        sys.exit(1)

    try:
        server.run_forever()
    except KeyboardInterrupt:
        print('\nShutting down server...')
    finally:
        server.close()


if __name__ == '__main__':
    main()
