#!/usr/bin/env python3

epilog = '''
This script parses the output of the "trace-cmd report" command, which is
used to convert ftrace data to a readable function graph.
https://trace-cmd.org/

Without any option, this program does nothing. More exactly, it parses its
input, saves each line in an internal representation, then iterates over each
record and prints it out in the same format as the input.

With -c (--cumulative_time_on_entry) it moves the timing values from the
function return to the function entry to make it easier to associate the time
and the function name.

The -s (--self_time_on_entry) option does the same as -c but in addition it
subtracts the time spend in the child functions, so that the time values
represent time spent in the function itself.

With -t (--top_functions), a list of function names is printed as well as the
total amount of time spent in each of them (not accounting for time spent in
the children). The list is sorted so that the function accounting for the most
time are shown first.

Examples

$ cat foobar.log
           |  foo() {
  2.019 us |    bar();
 33.124 us |    }
 20.003 us |  foo();
$ cat foobar.log | ./parse_trace.py -c
          33.124 us   |  foo() {
           2.019 us   |    bar();
                      |  }
          20.003 us   |  foo();
$ cat foobar.log | ./parse_trace.py -s
          31.105 us   |  foo() {
           2.019 us   |    bar();
                      |  }
          20.003 us   |  foo();
$ cat foobar.log | ./parse_trace.py -t
foo                           51.108 us
bar                            2.019 us
'''

import argparse
import re
import sys
from enum import Enum

def get_args():
    parser = argparse.ArgumentParser(
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description='Reformat output of the "trace-cmd report" command',
        epilog=epilog)
    group = parser.add_mutually_exclusive_group()
    group.add_argument('-c', '--cumulative_time_on_entry', action="store_true",
                       help='Move timing values from lines with a closing '
                       'brace to the matching line with a function name and '
                       'an opening brace. In other words, report the '
                       'cumulative time on function entry.')
    group.add_argument('-s', '--self_time_on_entry', action="store_true",
                       help='Compute time spent in each function excluding '
                       'its children, and print the time on the line that '
                       'represents the function entry')
    group.add_argument('-t', '--top_functions', action="store_true",
                       help='Compute and report the total time spent in each '
                       'function, not accounting for children. The output is '
                       'sorted by time (highest to lowest).')
    parser.add_argument('-d', '--debug', action="store_true",
                        help='Add debug messages to output')
    parser.add_argument('-p', '--progress', action="store_true",
                        help='Show progress to stderr (useful with large files)')
    parser.add_argument('-q', '--no_warn', action="store_true",
                        help='Do not print warnings')
    parser.add_argument('-m', '--min_one_nano', action="store_true",
                        help='Consider than all timings of 0.000 us are '
                        'actually one nanosecond (0.001 us). may allows very '
                        'short calls performed repeatedly too remain visible '
                        'in the output.')
    return parser.parse_args()

class OutFormat(Enum):
    NO_CHANGE = 0
    CUMULATIVE_TIME_ON_ENTRY = 1
    SELF_TIME_ON_ENTRY = 2
    TOP_FUNCTIONS = 3

# Calls are indented with two spaces (used to compute the call stack depth
# for each function)
INDENT = 2

PARSE_ERR = 1
STRUCTURE_ERR = 1

CLEAR = "\x1b[2K"

class CallType(Enum):
    UNKNOWN = 0
    OPENING = 1
    LEAF = 2
    CLOSING = 3

records = []

def debug_dump(rec):
    global args
    if args.debug:
        for r in rec:
            print(r)

def parse_traces():
    global args
    global max_depth

    if args.progress or args.debug:
        print("Parsing...", file=sys.stderr)

    inp = sys.stdin
    
    # One record per line in the input file that is a function entry
    lineno = 0
    status = 0
    recno = 0
    max_depth = 0
    need_nl = False
    for line in inp:
        lineno = lineno + 1
        if args.progress and lineno % 10000 == 0:
            print("#", end="", file=sys.stderr)
            sys.stderr.flush()
            need_nl = True
        t = -1
        mult = 1
        what = ""
        fn = ""
        depth = -1
        call_type = CallType.UNKNOWN
        if re.match('cpus=.*', line):
            continue;
        m = re.match(' +(?P<flag>\\+|!|#|) *(?P<t>[0-9\\.]*) +(?P<mult>|us|ms) *+\\|(?P<what>.*)', line)
        if not m:
            error = f"Syntax error #1"
            status = PARSE_ERR
        else:
            s_t = m.group("t")
            if s_t:
                t = float(s_t)
            s_mult = m.group("mult")
            if s_mult:
                if s_mult == "us":
                    mult = 1;
                elif s_mult == "ms":
                    mult = 1000;
                else:
                    error = f"Unknown seconds multiplier: {s_mult}"
                    status = PARSE_ERR;
            flag = m.group("flag")
            what = m.group("what")
            if not what:
                error = f"Syntax error #2"
                status = PARSE_ERR
            else:
                m = re.match('(?P<spc> +)((?P<fn>[a-zA-Z0-9_-]+)(?P<op>\\(\\);|\\(\\) {)|(?P<clo>}))', what)
                if not m:
                    error = f"Syntax error #3"
                    status = PARSE_ERR
                elif m.group("clo") == "}":
                    call_type = CallType.CLOSING
                elif m.group("op") == "() {":
                    call_type = CallType.OPENING
                else:
                    call_type = CallType.LEAF
                depth = len(m.group("spc")) / INDENT;
                if max_depth < depth:
                    max_depth = depth
                if call_type == CallType.CLOSING:
                    # Closing braces are indented one notch more
                    depth = depth - 1
                if call_type == CallType.OPENING or call_type == CallType.LEAF:
                    fn = m.group("fn")
                else:
                    fn = ""

        if status != 0:
            print(f'Error line {lineno}: {error}')
            print(f">> {line}")
            sys.exit(1)

        if args.min_one_nano:
            if t == 0:
                t = 0.001

        records.append({"recno": recno, "lineno": lineno, "flag": flag, "t": t, "fn": fn,
                        "call_type": call_type, "depth": depth})
        recno = recno + 1

    if need_nl:
        print("", file = sys.stderr)
    debug_dump(records)

# Input: any record list
def print_output(records):
    global args
    if args.progress or args.debug:
        print("Writing reformatted data...", file=sys.stderr)
    need_nl = False
    for i in range(len(records)):
        if args.progress and i % 10000 == 0:
            print("#", end="", file=sys.stderr)
            sys.stderr.flush()
            need_nl = True
        r = records[i]
        if r["depth"] == -1:
            continue
        if r['t'] == -1:
            s_t = " " * 16
        else:
            flag = r['flag']
            formatted = f"{flag} {r['t']:.3f} us"
            s_t = f"{formatted:>16}"
        if args.debug:
            print(f"#{r['recno']:<4} [{int(r['depth']):2}] ", end="")
        print(f"{s_t}   |" + "  " * int(r["depth"]), end="")
        if r["call_type"] == CallType.CLOSING:
            print("}")
        else:
            print(f"{r['fn']}()", end="")
            if r["call_type"] == CallType.OPENING:
                print(" {")
            else:
                print(";")
    if need_nl:
        print("", file=sys.stderr)

# Input: records after parse_traces()
def update_for_cumulative_time_on_entry(records):
    global args
    if args.progress or args.debug:
        print("Updating records for time on entry...", file=sys.stderr)

    need_nl = False
    r = records
    for i in range(len(r)):
        if args.progress and i % 10000 == 0:
            print("#", end="", file=sys.stderr)
            sys.stderr.flush()
            need_nl = True
        if r[i]["t"] == -1 and r[i]["call_type"] == CallType.OPENING:
            found = False
            for j in range(i + 1, len(r)):
                if (r[j]["call_type"] == CallType.CLOSING and
                    r[j]["depth"] == r[i]["depth"]):
                    if args.debug:
                        print(f"Moving time and flag from record {j} to record {i}")
                    r[i]["t"] = r[j]["t"]
                    r[i]["flag"] = r[j]["flag"]
                    r[j]["t"] = -1
                    r[j]["flag"] = ""
                    found = True
                    break
            if found:
                if args.debug:
                    print(f"Closing brace found for record #{i}")
            else:
                if not args.no_warn:
                    print(f"Warning: no matching '}}' for record #{i} "
                          f"(function {r[i]['fn']})",
                          file = sys.stderr)
    if need_nl:
        print("", file=sys.stderr)
    debug_dump(r)

# Input: records after update_for_cumulative_time_on_entry() and calls to
# update_level_for_self_time_on_entry() for lower depths
def update_level_for_self_time_on_entry(depth, records):
    global args
    if args.progress or args.debug:
        print(f"Updating records for self time on entry (depth: {depth})...",
              file=sys.stderr)

    need_nl = False
    r = records
    for i in range(len(r)):
        if args.progress and i % 10000 == 0:
            print("#", end="", file=sys.stderr)
            sys.stderr.flush()
            need_nl = True
        d = r[i]["depth"]
        if (r[i]["call_type"] == CallType.OPENING and r[i]["t"] != -1 and
            d == depth):
            if args.debug:
                print(f"Record #{i} cumulative time {r[i]['t']}...",
                      file=sys.stderr)
            # Move forward, subtract children time
            for j in range(i + 1, len(r)):
                if r[j]["depth"] <= d:
                    break;
                if r[j]["depth"] > d + 1:
                    continue;
                if r[j]["t"] == -1:
                    if r[j]["call_type"] == CallType.CLOSING:
                        continue;
                    else:
                        # This child has no timing information so we can't
                        # compute the parent time
                        r[i]["t"] = -1
                        if not args.no_warn:
                            print("Warning: no time information for record i"
                                  f"#{j} child of #{i}. Skipping.",
                                  file=sys.stderr)
                    break
                r[i]["t"] -= r[j]["t"]
                if args.debug:
                    print(f"...substracting time from record #{j} "
                          f"(- {r[j]['t']}) => {r[i]["t"]:.3f}", file=sys.stderr)
            if r[i]["t"] <= 0:
                r[i]["t"] = 0
            if args.min_one_nano:
                if r[i]["t"] == 0:
                    r[i]["t"] = 0.001

    if need_nl:
        print("", file=sys.stderr)
    debug_dump(r)

# Input: records after update_for_time_on_entry()
def update_for_self_time_on_entry(records):
    global max_depth
    for i in range(int(max_depth)):
        update_level_for_self_time_on_entry(i, records)

def make_totals(records):
    totals = {}
    global args
    if args.progress or args.debug:
        print("Making totals...", file=sys.stderr)

    need_nl = False
    for i in range(len(records)):
        if args.progress and i % 10000 == 0:
            print("#", end="", file=sys.stderr)
            sys.stderr.flush()
            need_nl = True
        r = records[i]
        fn = r["fn"]
        if fn and r["t"] != -1:
            if not fn in totals:
                totals[fn] = 0.0
            totals[fn] += r["t"]
    if need_nl:
        print("", file=sys.stderr)

    sorted_totals = {}
    for key in sorted(totals, key=totals.get, reverse=True):
        sorted_totals[key] = totals[key]

    return sorted_totals

def display_totals(totals):
    for k in totals:
        print(f"{k:25} {totals[k]:10.3f} us")
    total = sum(totals.values())
    print(f"Total time: {total:.3f} us")

def process(fmt):
    parse_traces()
    if fmt == OutFormat.NO_CHANGE:
        print_output(records)
    elif fmt == OutFormat.CUMULATIVE_TIME_ON_ENTRY:
        update_for_cumulative_time_on_entry(records)
        print_output(records)
    elif fmt == OutFormat.SELF_TIME_ON_ENTRY:
        update_for_cumulative_time_on_entry(records)
        update_for_self_time_on_entry(records)
        print_output(records)
    elif fmt == OutFormat.TOP_FUNCTIONS:
        update_for_cumulative_time_on_entry(records)
        update_for_self_time_on_entry(records)
        totals = make_totals(records)
        display_totals(totals)
    else:
        print("Error: unknown output format")

def main():
    global args
    args = get_args()
    if args.cumulative_time_on_entry:
        fmt = OutFormat.CUMULATIVE_TIME_ON_ENTRY
    elif args.self_time_on_entry:
        fmt = OutFormat.SELF_TIME_ON_ENTRY
    elif args.top_functions:
        fmt = OutFormat.TOP_FUNCTIONS
    else:
        fmt = OutFormat.NO_CHANGE
    process(fmt)

if __name__ == "__main__":
    main()
