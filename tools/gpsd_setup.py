"""Interactive gpsd setup — detects devices, writes /etc/default/gpsd, restarts gpsd."""

import os
import subprocess
import sys

import click

CANDIDATE_DEVICES = [
    '/dev/ttyUSB0', '/dev/ttyUSB1',
    '/dev/ttyACM0', '/dev/ttyACM1',
    '/dev/ttyAMA0', '/dev/ttyS0',
]

BAUD_RATES = ['4800', '9600', '38400', '115200']

GPSD_CONFIG_PATH = '/etc/default/gpsd'

GPSD_CONFIG_TEMPLATE = """\
# gpsd configuration — managed by gps-dashboard setup tool
START_DAEMON="true"
GPSD_OPTIONS="-n"
DEVICES="{device}"
USBAUTO="true"
GPSD_SOCKET="/var/run/gpsd.sock"
"""


def detect_devices():
    return [d for d in CANDIDATE_DEVICES if os.path.exists(d)]


def read_current_config():
    try:
        with open(GPSD_CONFIG_PATH) as f:
            return f.read()
    except FileNotFoundError:
        return None


def write_config(device, baud):
    config = GPSD_CONFIG_TEMPLATE.format(device=device)
    if baud != '4800':
        # gpsd can usually auto-detect, but we can hint via -s flag
        config = config.replace('GPSD_OPTIONS="-n"', f'GPSD_OPTIONS="-n -s {baud}"')
    try:
        result = subprocess.run(
            ['sudo', 'tee', GPSD_CONFIG_PATH],
            input=config, text=True, capture_output=True
        )
        if result.returncode != 0:
            click.echo(f"Error writing config: {result.stderr}", err=True)
            return False
        return True
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        return False


def restart_gpsd():
    try:
        subprocess.run(['sudo', 'systemctl', 'restart', 'gpsd'], check=True, timeout=15)
        return True
    except Exception as e:
        click.echo(f"Failed to restart gpsd: {e}", err=True)
        return False


@click.command()
@click.option('--device', default=None, help='GPS device path (skip auto-detect)')
@click.option('--baud', default=None, type=click.Choice(BAUD_RATES), help='Baud rate')
@click.option('--validate', is_flag=True, default=True, help='Run validation after setup')
def main(device, baud, validate):
    """Interactive gpsd setup for gps-dashboard."""
    click.echo('=== gpsd Setup ===\n')

    # Show current config if present
    current = read_current_config()
    if current:
        click.echo('Current /etc/default/gpsd:')
        click.echo(current.strip())
        click.echo()

    # Device selection
    if not device:
        detected = detect_devices()
        if detected:
            click.echo(f"Detected devices: {', '.join(detected)}")
            device = click.prompt(
                'Select device',
                default=detected[0],
                type=click.Choice(detected + ['other'])
            )
            if device == 'other':
                device = click.prompt('Enter device path')
        else:
            click.echo('No serial devices detected in standard locations.')
            device = click.prompt('Enter device path manually (e.g. /dev/ttyUSB0)')

    if not os.path.exists(device):
        click.echo(f"Warning: {device} does not currently exist.", err=True)
        if not click.confirm('Continue anyway?', default=False):
            sys.exit(1)

    # Baud rate selection
    if not baud:
        click.echo('\nCommon GPS baud rates:')
        click.echo('  4800   — NMEA default (most USB dongles)')
        click.echo('  9600   — common alternative')
        click.echo('  115200 — high-speed / u-blox modules')
        baud = click.prompt('Baud rate', default='9600', type=click.Choice(BAUD_RATES))

    click.echo(f'\nConfiguration:')
    click.echo(f'  Device: {device}')
    click.echo(f'  Baud:   {baud}')
    click.confirm('\nWrite config and restart gpsd?', default=True, abort=True)

    click.echo('\nWriting /etc/default/gpsd…')
    if not write_config(device, baud):
        sys.exit(1)
    click.echo('  Done.')

    click.echo('Restarting gpsd…')
    if not restart_gpsd():
        sys.exit(1)
    click.echo('  Done.\n')

    if validate:
        click.echo('Running validation (may take up to 15s for GPS fix)…\n')
        # Import here so the script can run without the full project installed
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from tools.gpsd_validate import run_all
        results = run_all()
        sys.exit(0 if all(ok for _, ok, _ in results) else 1)


if __name__ == '__main__':
    main()
