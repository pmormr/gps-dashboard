"""Interactive chrony NTP setup — GPS SHM only or GPS+PPS."""

import os
import shutil
import subprocess
import sys

import click

REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHRONY_CONF = '/etc/chrony/chrony.conf'
MODULES_FILE = '/etc/modules'
BOOT_CONFIG = '/boot/firmware/config.txt'  # Pi 4/5; older Pi uses /boot/config.txt

PPS_GPIO_PIN_DEFAULT = 18


def _run(cmd, **kwargs):
    return subprocess.run(cmd, **kwargs)


def _sudo_write(path, content):
    r = _run(['sudo', 'tee', path], input=content, text=True,
             capture_output=True)
    if r.returncode != 0:
        click.echo(f"Error writing {path}: {r.stderr}", err=True)
        return False
    return True


def _apt_install(package):
    click.echo(f"Installing {package}…")
    r = _run(['sudo', 'apt-get', 'install', '-y', package],
             capture_output=False)
    return r.returncode == 0


def _service(action, name):
    r = _run(['sudo', 'systemctl', action, name],
             capture_output=True, timeout=15)
    return r.returncode == 0


def ensure_chrony():
    if shutil.which('chronyc'):
        return True
    click.echo('chrony not found.')
    if click.confirm('Install chrony now?', default=True):
        return _apt_install('chrony')
    return False


def setup_gps_only():
    src = os.path.join(REPO_DIR, 'deploy', 'chrony-gps-only.conf')
    with open(src) as f:
        config = f.read()

    click.echo(f'\nWriting {CHRONY_CONF} (GPS-only mode)…')
    if not _sudo_write(CHRONY_CONF, config):
        return False

    click.echo('Restarting chrony…')
    if not _service('restart', 'chrony'):
        click.echo('Failed to restart chrony.', err=True)
        return False

    _service('enable', 'chrony')
    return True


def setup_gps_pps(gpio_pin):
    # 1. Write chrony config
    src = os.path.join(REPO_DIR, 'deploy', 'chrony-gps-pps.conf')
    with open(src) as f:
        config = f.read()

    click.echo(f'\nWriting {CHRONY_CONF} (GPS+PPS mode)…')
    if not _sudo_write(CHRONY_CONF, config):
        return False

    # 2. Add pps-gpio to /etc/modules
    click.echo('Enabling pps-gpio kernel module…')
    try:
        with open(MODULES_FILE) as f:
            modules = f.read()
    except FileNotFoundError:
        modules = ''

    if 'pps-gpio' not in modules:
        if not _sudo_write(MODULES_FILE, modules.rstrip() + '\npps-gpio\n'):
            return False

    # 3. Add pps-gpio overlay to boot config
    overlay_line = f'dtoverlay=pps-gpio,gpiopin={gpio_pin}'
    boot_config_path = BOOT_CONFIG if os.path.exists(BOOT_CONFIG) else '/boot/config.txt'
    click.echo(f'Configuring PPS GPIO overlay in {boot_config_path}…')
    try:
        with open(boot_config_path) as f:
            boot_cfg = f.read()
    except FileNotFoundError:
        click.echo(f"Could not read {boot_config_path}. You may need to add '{overlay_line}' manually.", err=True)
        boot_cfg = None

    if boot_cfg is not None and overlay_line not in boot_cfg:
        if not _sudo_write(boot_config_path, boot_cfg.rstrip() + f'\n{overlay_line}\n'):
            return False
        click.echo(f'  Added: {overlay_line}')
        click.echo()
        click.echo('  *** A reboot is required for PPS GPIO to take effect. ***')
        click.echo('  After rebooting, run this setup script again or restart chrony.')
    elif boot_cfg is not None:
        click.echo(f'  Already present: {overlay_line}')

    # 4. Restart chrony
    click.echo('Restarting chrony…')
    if not _service('restart', 'chrony'):
        click.echo('Failed to restart chrony.', err=True)
        return False

    _service('enable', 'chrony')
    return True


@click.command()
@click.option('--mode', type=click.Choice(['gps-only', 'gps-pps']),
              default=None, help='Skip mode prompt')
@click.option('--gpio-pin', default=PPS_GPIO_PIN_DEFAULT, show_default=True,
              help='GPIO pin number for PPS signal (GPS+PPS mode only)')
@click.option('--validate/--no-validate', default=True,
              help='Run validation after setup')
def main(mode, gpio_pin, validate):
    """Configure chrony to use GPS as the NTP time source."""
    click.echo('=== NTP Setup (chrony + GPS) ===\n')

    if not ensure_chrony():
        click.echo('chrony is required. Aborting.', err=True)
        sys.exit(1)

    if not mode:
        click.echo('Available modes:')
        click.echo('  gps-only  USB GPS dongle, no PPS — ~100ms accuracy, stratum 10')
        click.echo('  gps-pps   Serial GPS with PPS signal — microsecond accuracy, stratum 1')
        click.echo()
        mode = click.prompt('Select mode', type=click.Choice(['gps-only', 'gps-pps']))

    click.echo()

    if mode == 'gps-only':
        ok = setup_gps_only()
    else:
        click.echo(f'PPS GPIO pin: {gpio_pin}')
        click.echo('Make sure your GPS serial device and PPS pin are wired correctly.')
        click.echo('Default PPS pin is GPIO 18. Change with --gpio-pin if needed.\n')
        click.confirm('Proceed with GPS+PPS setup?', default=True, abort=True)
        ok = setup_gps_pps(gpio_pin)

    if not ok:
        sys.exit(1)

    click.echo('\nSetup complete.\n')

    if validate:
        click.echo('Running validation…\n')
        sys.path.insert(0, REPO_DIR)
        from tools.ntp_validate import run_all
        results = run_all(check_pps=(mode == 'gps-pps'))
        sys.exit(0 if all(ok for _, ok, _ in results) else 1)


if __name__ == '__main__':
    main()
