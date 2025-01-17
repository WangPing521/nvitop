# This file is part of nvitop, the interactive NVIDIA-GPU process viewer.
# License: GNU GPL version 3.

# pylint: disable=missing-module-docstring,missing-function-docstring

import argparse
import curses
import os
import sys

from nvitop.core import nvml, HostProcess, boolify
from nvitop.gui import Top, Device, libcurses, setlocale_utf8, colored, set_color, USERNAME
from nvitop.version import __version__


TTY = (sys.stdin.isatty() and sys.stdout.isatty())


def parse_arguments():  # pylint: disable=too-many-branches,too-many-statements
    coloring_rules = '{} < th1 %% <= {} < th2 %% <= {}'.format(colored('light', 'green'),
                                                               colored('moderate', 'yellow'),
                                                               colored('heavy', 'red'))
    parser = argparse.ArgumentParser(prog='nvitop', description='An interactive NVIDIA-GPU process viewer.',
                                     formatter_class=argparse.RawTextHelpFormatter, add_help=False)
    parser.add_argument('--help', '-h', dest='help', action='help', default=argparse.SUPPRESS,
                        help='Show this help message and exit.')
    parser.add_argument('--version', '-V', dest='version', action='version', version='%(prog)s {}'.format(__version__),
                        help="Show %(prog)s's version number and exit.")
    parser.add_argument('--once', '-1', dest='once', action='store_true',
                        help='Report query data only once.')
    parser.add_argument('--monitor', '-m', dest='monitor', type=str, default=argparse.SUPPRESS,
                        nargs='?', choices=['auto', 'full', 'compact'],
                        help='Run as a resource monitor. Continuously report query data and handle user inputs.\n'
                             'If the argument is omitted, the value from `NVITOP_MONITOR_MODE` will be used.\n'
                             '(default fallback mode: auto)')
    parser.add_argument('--ascii', '--no-unicode', '-U', dest='ascii', action='store_true',
                        help='Use ASCII characters only, which is useful for terminals without Unicode support.')

    coloring = parser.add_argument_group('coloring')
    coloring.add_argument('--force-color', dest='force_color', action='store_true',
                          help='Force colorize even when `stdout` is not a TTY terminal.')
    coloring.add_argument('--light', action='store_true',
                          help='Tweak visual results for light theme terminals in monitor mode.\n'
                               'Set variable `NVITOP_MONITOR_THEME="light"` on light terminals for convenience.')
    gpu_thresholds = Device.GPU_UTILIZATION_THRESHOLDS
    coloring.add_argument('--gpu-util-thresh', type=int, nargs=2, choices=range(1, 100), metavar=('th1', 'th2'),
                          help='Thresholds of GPU utilization to determine the load intensity.\n' +
                               'Coloring rules: {}.\n'.format(coloring_rules) +
                               '( 1 <= th1 < th2 <= 99, defaults: {} {} )'.format(*gpu_thresholds))
    memory_thresholds = Device.MEMORY_UTILIZATION_THRESHOLDS
    coloring.add_argument('--mem-util-thresh', type=int, nargs=2, choices=range(1, 100), metavar=('th1', 'th2'),
                          help='Thresholds of GPU memory percent to determine the load intensity.\n' +
                               'Coloring rules: {}.\n'.format(coloring_rules) +
                               '( 1 <= th1 < th2 <= 99, defaults: {} {} )'.format(*memory_thresholds))

    device_filtering = parser.add_argument_group('device filtering')
    device_filtering.add_argument('--only', '-o', dest='only', type=int, nargs='+', metavar='idx',
                                  help='Only show the specified devices, suppress option `--only-visible`.')
    device_filtering.add_argument('--only-visible', '-ov', dest='only_visible', action='store_true',
                                  help='Only show devices in environment variable `CUDA_VISIBLE_DEVICES`.')

    process_filtering = parser.add_argument_group('process filtering')
    process_filtering.add_argument('--compute', '-c', dest='compute', action='store_true',
                                   help="Only show GPU processes with the compute context. (type: 'C' or 'C+G')")
    process_filtering.add_argument('--graphics', '-g', dest='graphics', action='store_true',
                                   help="Only show GPU processes with the graphics context. (type: 'G' or 'C+G')")
    process_filtering.add_argument('--user', '-u', dest='user', type=str, nargs='*', metavar='USERNAME',
                                   help='Only show processes of the given users (or `$USER` for no argument).')
    process_filtering.add_argument('--pid', '-p', dest='pid', type=int, nargs='+', metavar='PID',
                                   help='Only show processes of the given PIDs.')

    args = parser.parse_args()

    if not args.light:
        args.light = (os.getenv('NVITOP_MONITOR_THEME', 'dark').lower() == 'light')
    if args.user is not None and len(args.user) == 0:
        args.user.append(USERNAME)
    if args.gpu_util_thresh is None:
        try:
            gpu_util_thresh = os.getenv('NVITOP_GPU_UTILIZATION_THRESHOLDS', None)
            gpu_util_thresh = list(map(int, gpu_util_thresh.split(',')))[:2]
            if len(gpu_util_thresh) != 2 or min(gpu_util_thresh) <= 0 or max(gpu_util_thresh) >= 100:
                raise ValueError
        except (ValueError, AttributeError):
            pass
        else:
            args.gpu_util_thresh = gpu_util_thresh
    if args.mem_util_thresh is None:
        try:
            mem_util_thresh = os.getenv('NVITOP_MEMORY_UTILIZATION_THRESHOLDS', None)
            mem_util_thresh = list(map(int, mem_util_thresh.split(',')))[:2]
            if len(mem_util_thresh) != 2 or min(mem_util_thresh) <= 0 or max(mem_util_thresh) >= 100:
                raise ValueError
        except (ValueError, AttributeError):
            pass
        else:
            args.mem_util_thresh = mem_util_thresh

    return args


def main():  # pylint: disable=too-many-branches,too-many-statements,too-many-locals
    args = parse_arguments()

    if args.force_color:
        set_color(True)

    messages = []
    if args.once and hasattr(args, 'monitor'):
        messages.append('ERROR: Both `--once` and `--monitor` switches are on.')
        del args.monitor

    if not args.once and not hasattr(args, 'monitor') and TTY \
            and boolify(os.getenv('NVITOP_MONITOR_ALWAYS', 'true'), default=True):
        args.monitor = None

    if hasattr(args, 'monitor') and not TTY:
        messages.append('ERROR: You must run monitor mode from a TTY terminal.')
        del args.monitor

    if hasattr(args, 'monitor') and args.monitor is None:
        mode = os.getenv('NVITOP_MONITOR_MODE', 'auto').lower()
        if mode not in ('auto', 'full', 'compact'):
            mode = 'auto'
        args.monitor = mode

    if not setlocale_utf8():
        args.ascii = True

    try:
        device_count = Device.count()
    except nvml.NVMLError_LibraryNotFound:  # pylint: disable=no-member
        return 1
    except nvml.NVMLError as e:  # pylint: disable=invalid-name
        print('{} {}'.format(colored('NVML ERROR:', color='red', attrs=('bold',)), e), file=sys.stderr)
        return 1

    if args.gpu_util_thresh is not None:
        Device.GPU_UTILIZATION_THRESHOLDS = tuple(sorted(args.gpu_util_thresh))
    if args.mem_util_thresh is not None:
        Device.MEMORY_UTILIZATION_THRESHOLDS = tuple(sorted(args.mem_util_thresh))

    if args.only is not None:
        indices = set(args.only)
        invalid_indices = indices.difference(range(device_count))
        indices.intersection_update(range(device_count))
        if len(invalid_indices) > 1:
            messages.append('ERROR: Invalid device indices: {}.'.format(sorted(invalid_indices)))
        elif len(invalid_indices) == 1:
            messages.append('ERROR: Invalid device index: {}.'.format(list(invalid_indices)[0]))
    elif args.only_visible:
        indices = Device.parse_cuda_visible_devices()
    else:
        indices = range(device_count)
    devices = Device.from_indices(sorted(set(indices)))

    filters = []
    if args.compute:
        filters.append(lambda process: 'C' in process.type)
    if args.graphics:
        filters.append(lambda process: 'G' in process.type)
    if args.user is not None:
        users = set(args.user)
        filters.append(lambda process: process.username in users)
    if args.pid is not None:
        pids = set(args.pid)
        filters.append(lambda process: process.pid in pids)

    top = None
    if hasattr(args, 'monitor') and len(devices) > 0:
        try:
            with libcurses(light_theme=args.light) as win:
                top = Top(devices, filters, ascii=args.ascii, mode=args.monitor, win=win)
                top.loop()
        except curses.error as e:  # pylint: disable=invalid-name
            if top is not None:
                raise
            messages.append('ERROR: Failed to initialize `curses` ({})'.format(e))

    if top is None:
        top = Top(devices, filters, ascii=args.ascii)
        if not sys.stdout.isatty():
            parent = HostProcess().parent()
            grandparent = (parent.parent() if parent is not None else None)
            if grandparent is not None and parent.name() == 'sh' and grandparent.name() == 'watch':
                print(
                    'HINT: You are running `nvitop` under `watch` command. Please try `nvitop -m` directly.',
                    file=sys.stderr
                )

    top.print()
    top.destroy()

    if len(nvml.UNKNOWN_FUNCTIONS) > 0:
        messages.append('ERROR: A FunctionNotFound error occurred while calling:')
        if len(nvml.UNKNOWN_FUNCTIONS) > 1:
            messages[-1] = messages[-1].replace('A FunctionNotFound error', 'Some FunctionNotFound errors')
        messages.extend([
            *list(map('    nvmlQuery({.__name__!r}, *args, **kwargs)'.format, nvml.UNKNOWN_FUNCTIONS)),
            ('Please verify whether the `{0}` package is compatible with your NVIDIA driver version.\n'
             'You can check the release history of `{0}` and install the compatible version manually.\n'
             'See {1} for more information.').format(
                colored('nvidia-ml-py', attrs=('bold',)),
                colored('https://github.com/XuehaiPan/nvitop#installation', attrs=('underline',))
            )
        ])
    if len(messages) > 0:
        for message in messages:
            if message.startswith('ERROR:'):
                message = message.replace('ERROR:', colored('ERROR:', color='red', attrs=('bold',)), 1)
            print(message, file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
