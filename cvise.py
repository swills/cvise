#!/usr/bin/env python3

import argparse
import logging
import multiprocessing
import os
import os.path
import shutil
import sys
import time
import datetime

import importlib.util

# If the cvise modules cannot be found
# add the known install location to the path
if importlib.util.find_spec("cvise") is None:
    script_path = os.path.dirname(os.path.realpath(__file__))
    sys.path.append(os.path.join(script_path, "..", "share"))

from cvise import CVise
from cvise.passes.abstract import AbstractPass
from cvise.utils.error import CViseError
from cvise.utils.error import MissingPassGroupsError
from cvise.utils.info import ExternalPrograms
from cvise.utils import testing
from cvise.utils import statistics

class DeltaTimeFormatter(logging.Formatter):
    def format(self, record):
        duration = datetime.datetime.utcfromtimestamp(record.relativeCreated / 1000)
        record.delta = duration.strftime("%H:%M:%S")
        return super().format(record)

def get_share_dir():
    script_path = os.path.dirname(os.path.realpath(__file__))

    # Test all known locations for the cvise directory
    share_dirs = [
            os.path.join(script_path, "..", "share", "cvise"),
            os.path.join(script_path, "cvise")
            ]

    for d in share_dirs:
        if os.path.isdir(d):
            return d

    raise CViseError("Cannot find cvise module directory!")

def get_libexec_dir():
    script_path = os.path.dirname(os.path.realpath(__file__))

    # Test all known locations for the cvise directory
    libexec_dirs = [
            os.path.join(script_path, "..", "libexec"),
            #FIXME: The programs are in sub directories
            os.path.join(script_path)
            ]

    for d in libexec_dirs:
        if os.path.isdir(d):
            return d

    raise CViseError("Cannot find libexec directory!")

def find_external_programs():
    programs = ExternalPrograms()
    libexec_dir = get_libexec_dir()

    for prog_key in ExternalPrograms.programs:
        prog = programs[prog_key]

        path = shutil.which(prog)

        if path is None:
            path = shutil.which(prog, path=libexec_dir)

        if path is not None:
            programs[prog_key] = path

    return programs

def get_pass_group_path(name):
    return os.path.join(get_share_dir(), "pass_groups", name + ".json")

def get_available_pass_groups():
    pass_group_dir = os.path.join(get_share_dir(), "pass_groups")

    if not os.path.isdir(pass_group_dir):
        raise MissingPassGroupsError()

    group_names = []

    for entry in os.listdir(pass_group_dir):
        path = os.path.join(pass_group_dir, entry)

        if not os.path.isfile(path):
            continue

        try:
            pass_group_dict = CVise.load_pass_group_file(path)
            CVise.parse_pass_group_dict(pass_group_dict, set(), None, None, None, None)
        except MissingPassGroupsError:
            logging.warning("Skipping file {}. Not valid pass group.".format(path))
        else:
            (name, _) = os.path.splitext(entry)
            group_names.append(name)

    return group_names

EPILOG_TEXT = """
available shortcuts:
  S - skip execution of the current pass
  D - toggle --print-diff option
"""

if __name__ == "__main__":
    try:
        core_count = multiprocessing.cpu_count()
    except NotImplementedError:
        core_count = 1

    parser = argparse.ArgumentParser(description="C-Vise", formatter_class=argparse.RawDescriptionHelpFormatter, epilog=EPILOG_TEXT)
    parser.add_argument("--n", "-n", type=int, default=core_count, help="Number of cores to use; C-Vise tries to automatically pick a good setting but its choice may be too low or high for your situation")
    parser.add_argument("--tidy", action="store_true", default=False, help="Do not make a backup copy of each file to reduce as file.orig")
    parser.add_argument("--shaddap", action="store_true", default=False, help="Suppress output about non-fatal internal errors")
    parser.add_argument("--die-on-pass-bug", action="store_true", default=False, help="Terminate C-Vise if a pass encounters an otherwise non-fatal problem")
    parser.add_argument("--sllooww", action="store_true", default=False, help="Try harder to reduce, but perhaps take a long time to do so")
    parser.add_argument("--also-interesting", metavar="EXIT_CODE", type=int, help="A process exit code (somewhere in the range 64-113 would be usual) that, when returned by the interestingness test, will cause C-Vise to save a copy of the variant")
    parser.add_argument("--debug", action="store_true", default=False, help="Print debug information")
    parser.add_argument("--log-level", type=str, choices=["INFO", "DEBUG", "WARNING", "ERROR"], default="INFO", help="Define the verbosity of the logged events")
    parser.add_argument("--log-file", type=str, help="Log events into LOG_FILE instead of stderr. New events are appended to the end of the file")
    parser.add_argument("--no-give-up", action="store_true", default=False, help="Don't give up on a pass that hasn't made progress for {} iterations".format(testing.TestManager.GIVEUP_CONSTANT))
    parser.add_argument("--print-diff", action="store_true", default=False, help="Show changes made by transformations, for debugging")
    parser.add_argument("--save-temps", action="store_true", default=False, help="Don't delete /tmp/cvise-xxxxxx directories on termination")
    parser.add_argument("--skip-initial-passes", action="store_true", default=False, help="Skip initial passes (useful if input is already partially reduced)")
    parser.add_argument("--remove-pass", help="Remove all instances of the specified passes from the schedule (comma-separated)")
    parser.add_argument("--timing", action="store_true", default=False, help="Print timestamps about reduction progress")
    parser.add_argument("--timing-since-start", action="store_true", default=False, help="Print timestamps since the start of a reduction")
    parser.add_argument("--timeout", type=int, nargs="?", const=300, help="Interestingness test timeout in seconds")
    parser.add_argument("--no-cache", action="store_true", default=False, help="Don't cache behavior of passes")
    parser.add_argument("--skip-key-off", action="store_true", default=False, help="Disable skipping the rest of the current pass when \"s\" is pressed")
    parser.add_argument("--max-improvement", metavar="BYTES", type=int, help="Largest improvement in file size from a single transformation that C-Vise should accept (useful only to slow C-Vise down)")
    passes_group = parser.add_mutually_exclusive_group()
    passes_group.add_argument("--pass-group", type=str, choices=get_available_pass_groups(), help="Set of passes used during the reduction")
    passes_group.add_argument("--pass-group-file", type=str, help="JSON file defining a custom pass group")
    parser.add_argument("--clang-delta-std", type=str, choices=["c++98", "c++11", "c++14", "c++17", "c++20"], help="Specify clang_delta C++ standard, it can rapidly speed up all clang_delta passes")
    parser.add_argument("--not-c", action="store_true", help="Don't run passes that are specific to C and C++, use this mode for reducing other languages")
    parser.add_argument("--list-passes", action="store_true", help="Print all available passes and exit")
    parser.add_argument("interestingness_test", metavar="INTERESTINGNESS_TEST", help="Executable to check interestingness of test cases")
    parser.add_argument("test_cases", metavar="TEST_CASE", nargs="+", help="Test cases")

    args = parser.parse_args()

    log_config = {}

    log_format = "%(levelname)s %(message)s"
    if args.timing:
        if args.timing_since_start:
            log_format = "%(delta)s " + log_format
        else:
            log_format = "%(asctime)s " + log_format

    if args.debug:
        log_config["level"] = logging.DEBUG
    else:
        log_config["level"] = getattr(logging, args.log_level.upper())

    if args.log_file is not None:
        log_config["filename"] = args.log_file

    logging.basicConfig(**log_config)
    syslog = logging.StreamHandler()
    formatter = DeltaTimeFormatter(log_format)
    syslog.setFormatter(formatter)
    logging.getLogger().handlers = []
    logging.getLogger().addHandler(syslog)

    pass_options = set()

    if sys.platform == "win32":
        pass_options.add(AbstractPass.Option.windows)

    if args.sllooww:
        pass_options.add(AbstractPass.Option.slow)

    if args.pass_group is not None:
        pass_group_file = get_pass_group_path(args.pass_group)
    elif args.pass_group_file is not None:
        pass_group_file = args.pass_group_file
    else:
        pass_group_file = get_pass_group_path("all")

    external_programs = find_external_programs()

    pass_group_dict = CVise.load_pass_group_file(pass_group_file)
    pass_group = CVise.parse_pass_group_dict(pass_group_dict, pass_options, external_programs,
            args.remove_pass, args.clang_delta_std, args.not_c)
    if args.list_passes:
        logging.info('Available passes:')
        logging.info('INITIAL PASSES')
        for p in pass_group["first"]:
            logging.info(str(p))
        logging.info('MAIN PASSES')
        for p in pass_group["main"]:
            logging.info(str(p))
        logging.info('CLEANUP PASSES')
        for p in pass_group["last"]:
            logging.info(str(p))

        sys.exit(0)
        logging.shutdown()

    pass_statistic = statistics.PassStatistic()

    test_manager = testing.TestManager(pass_statistic, args.interestingness_test, args.timeout,
            args.save_temps, args.test_cases, args.n, args.no_cache, args.skip_key_off, args.shaddap,
            args.die_on_pass_bug, args.print_diff, args.max_improvement, args.no_give_up, args.also_interesting)

    reducer = CVise(test_manager)

    reducer.tidy = args.tidy

    # Track runtime
    if args.timing:
        time_start = time.monotonic()

    try:
        reducer.reduce(pass_group, skip_initial=args.skip_initial_passes)
    except CViseError as err:
        print(err)
    else:
        print("pass statistics:")

        for item in pass_statistic.sorted_results:
            print("method {pass} worked {worked} times and failed {failed} times".format(**item))

        for test_case in test_manager.sorted_test_cases:
            with open(test_case) as test_case_file:
                print(test_case_file.read())

    if args.timing:
        time_stop = time.monotonic()
        print("Runtime: {} seconds".format(round((time_stop - time_start))))

    logging.shutdown()
