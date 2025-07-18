# Hikvision TFTP Recovery Tool

A Python utility to unbrick Hikvision cameras and NVRs using TFTP firmware recovery. This tool handles the custom Hikvision TFTP handshake and serves firmware files to restore bricked devices.

## Installation

This project uses [uv](https://docs.astral.sh/uv/) for Python package management. Install dependencies with:

    $ uv sync

## Quick Start

1. **Configure your network interface:**
   ```bash
   # Linux
   sudo ifconfig eth0:0 192.0.0.128

   # macOS
   sudo ifconfig en0 alias 192.0.0.128 255.255.255.0
   ```

2. **Download your device's firmware:**
   ```bash
   curl -o digicap.dav <firmware_url>
   ```

3. **Run the TFTP server:**
   ```bash
   uv run hikvision_tftpd.py
   ```

4. **Power cycle your device** and wait for the firmware transfer to complete

5. **Stop the server** with Ctrl+C when done

## How It Works

Hikvision devices use a custom TFTP recovery process that requires two steps:

1. **Handshake**: The device sends a packet to port 9978 and expects an echo response
2. **Transfer**: The device requests firmware via standard TFTP on port 69

This tool handles both steps automatically, providing a complete recovery solution.

## Device Compatibility

Different Hikvision models expect different network configurations:

| Device IP    | Server IP    | Firmware File |
| ------------ | ------------ | ------------- |
| 192.0.0.64   | 192.0.0.128  | `digicap.dav` |
| 172.9.18.100 | 172.9.18.80  | `digicap.mav` |

The tool defaults to the first configuration. For devices using the second configuration, use:

    $ uv run hikvision_tftpd.py --server-ip=172.9.18.80 --filename=digicap.mav

## Troubleshooting

If your device doesn't respond after restarting, it may expect different IP addresses. Use tcpdump to see what your device is looking for:

    $ sudo tcpdump -i eth0 -vv -e -nn ether proto 0x0806

Look for ARP requests to identify the expected server IP address.

## Development

Install development dependencies:

    $ uv sync --dev

Run tests:

    $ uv run python -m unittest hikvision_tftpd_test.py -v

Check code formatting and linting:

    $ uv run ruff check .
    $ uv run ruff format --check .

Run type checking:

    $ uv run mypy hikvision_tftpd.py --ignore-missing-imports

## Getting Help

If you need assistance, please open an issue or check the [discussion thread](https://www.ipcamtalk.com/showthread.php/3647-Hikvision-DS-2032-I-Console-Recovery) for more details.
