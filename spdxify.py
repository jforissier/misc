#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-2-Clause
#
# Copyright (c) 2017, Linaro Limited
#
# This is a tool quickly put together to help check license headers in source
# files in the OP-TEE project and modify them if needed. Its main purpose is
# to automate the addition of SPDX license identifier [1] to a large collection
# of source files.
#
# The rules are the following (they comply with the recommendations of the
# REUSE Initiative [2] defined by the Free Software Foundation Europe, except
# for the License-Filename tag which is not used here):
#
# 1. New source files
#   1.1 Shall contain at least one copyright line
#   1.2 Shall contain exactly one SPDX license identifier
#   1.3 Shall not contain the mention 'All rights reserved' or similar
#   1.4 The SPDX license identifier shall be the first possible line in the
#       file which can contain a comment.
#   1.5 The comment style for the SPDX line in C files shall be //.
#   1.6 Files imported from external projects are not new files. The rules for
#       existing files below apply.
#   1.7 Example:
#       // SPDX-License-Identifier: BSD-2-Clause
#       /*
#        * Copyright (c) 2017, Linaro Limited
#        */
#
# 2. Existing source files
#   2.1 SPDX license identifiers shall be added to existing files and reflect
#       any pre-existing license notice.
#   2.2 Full text license notices shall be removed when possible, that is: by
#       the copyright holder only.
#   2.3 The mention: 'All rights reserved' or similar shall be removed when
#       possible, that is: by the copyright holder only.
#
# Usage examples:
#
# $ ./spdxify.py --mistagged-only TOPDIR
#   ... shows a list of file which miss one or more tag
#
# $ ./spdxify.py --add-spdx TOPDIR
#   ... fixes the above issues by adding the proper SPDX tag(s) to the files
#
# $ ./spdxify.py --linaro-only --strip-arr TOPDIR
#   ... removes the 'All rights reserved' text from the Linaro files
#
# $ ./spdxify.py --linaro-only --strip-license-text TOPDIR
#   ... removes the full license text from the Linaro files
#
# [1] https://spdx.org/licenses/
# [2] https://reuse.software/practices/

import argparse
import glob
import os
import re
import shutil
import tempfile


BSDStart = re.compile('Redistribution and use.*in source and binary forms')
BSDEnd = 'SUCH DAMAGE.'
BSDClause1 = 'Redistributions of source code must retain the above'
BSDClause2 = 'Redistributions in binary form must reproduce the above'
BSDClause3 = 'The name of the author may not be used to endorse'
BSDClause3_1 = 'Neither the name of'
BSDClause3_2 = 'be used to endorse or promote'
SPDX_ID = re.compile(r'SPDX-License-Identifier: \(?(?P<SPDX_ID>[^\)]+)\)?')
Apache2 = 'Apache License, Version 2.0'
ZlibStart = 'This software is provided \'as-is\', without any express or implied'
ZlibClause1 = 'The origin of this software must not be misrepresented'
ZlibClause2 = 'Altered source versions must be plainly marked as such'
ZlibClause3 = 'This notice may not be removed or altered from any source distribution'
ZlibEnd = ZlibClause3
ZlibRef = 'see copyright notice in zlib.h'
ISCStart = 'Permission to use, copy, modify, and distribute this software'
ISCEnd = 'IN CONNECTION WITH THE USE OR PERFORMANCE OF THIS SOFTWARE'
GPLStart = 'terms of the GNU General Public License'
GPLv2OrLater = 'either version 2'
GPLEnd = 'You should have received a copy'
MIT = 'substantial portions of the Software'
ISC = 'PERFORMANCE OF THIS SOFTWARE'

AllRightsReserved = 'All rights reserved'

def identify_license(text, file):
    hasBSDStart = False
    hasBSDClause1 = False
    hasBSDClause2 = False
    hasBSDClause3 = False
    hasGPLStart = False
    hasGPLv2OrLater = False

    for line in text:
        if re.search(BSDStart, line):
            hasBSDStart = True
        if BSDClause1 in line:
            hasBSDClause1 = True
        if BSDClause2 in line:
            hasBSDClause2 = True
        if BSDClause3 in line or BSDClause3_1 in line or BSDClause3_2 in line:
            hasBSDClause3 = True
        if ZlibStart in line:
           return 'Zlib'
        if ISCStart in line:
           return 'ISC'
        if GPLStart in line:
           hasGPLStart = True
        if GPLv2OrLater in line:
           hasGPLv2OrLater = True

    if hasBSDStart:
        bsd = False
        if hasBSDClause1:
            if hasBSDClause2:
                if hasBSDClause3:
                    bsd =  'BSD-3-Clause'
                else:
                    bsd = 'BSD-2-Clause'
            else:
                if hasBSDClause3:
                    bsd = 'BSD-Source-Code'
        if not bsd: 
            print('Error: unknown BSD-like license in file:', file)
            exit(1)
        return bsd

    if hasGPLStart and hasGPLv2OrLater:
        return 'GPL-2.0+'

    # Unknown license
    return ''


def has_hash_comment_style(file):
    return (file.endswith('.mk') or file.endswith('Makefile') or
            file.endswith('.py') or file.endswith('.sh'))


def has_c_comment_style(file):
    return (file.endswith('.c') or file.endswith('.h') or
            file.endswith('.ld') or file.endswith('.S'))

def comment_prefix(file):
    if has_c_comment_style(file):
        return ' * '
    if has_hash_comment_style(file):
        return '# '
    return ''


def comment_prefix_for_SPDX(file):
    if has_c_comment_style(file):
        return '// '
    if has_hash_comment_style(file):
        return '# '
    return ''


def file_props(file):
    props = { 'licenses': set([]), 'lic_start_end': {}, 'SPDX_ID': '',
                'arr': False, 'spdx_insertion': 1,
                'multiple_copyright_blocks': False, 'comment_prefix_for_SPDX':'',
                'has_dual_license': False, 'operator': ' AND ', 'file': file }
    commentPrefix = ''
    lineno = 0
    hasLinaroCopyright = False
    hasOtherCopyright = False
    in_license = 0
    text = []
    last_copyright = 0
    copyright_state = 0 # 0: initial, 1: in first copyright block, 2: after
    blank_line_pending = False
    blank_line_in_first_copyright_block = False

    commentPrefix = comment_prefix(file)
    props['comment_prefix_for_SPDX'] = comment_prefix_for_SPDX(file)

    if commentPrefix == '':
        print('Error: unknown comment style for file: ' + file)
        exit(1)
    else:
        props['commentPrefix'] = commentPrefix

    with open(file) as f:
        for line in f:
            lineno = lineno + 1
            if lineno == 1 and line[:2] == '#!':
                props['spdx_insertion'] = 2

            if 'Copyright' in line:
                if 'Linaro' in line:
                    hasLinaroCopyright = True
                else:
                    hasOtherCopyright = True
                if copyright_state == 0:
                    copyright_state = 1
                elif copyright_state == 2:
                    props['multiple_copyright_blocks'] = True

            if 'dual licen' in line:
                props['has_dual_license'] = True
                props['operator'] = ' OR '

            if copyright_state == 1:
                if 'Copyright' in line or AllRightsReserved in line:
                    last_copyright = lineno
                    if has_c_comment_style(file) and line.strip()[0] != '*':
                        n = len(line) - len(line.strip())
                        props['commentPrefix'] = line[:n-1]
                    if blank_line_pending:
                        blank_line_in_first_copyright_block = True
                elif is_blank(line, props):
                    if copyright_state == 1:
                        blank_line_pending = True
                else:
                    copyright_state = 2

            if (re.search(BSDStart, line) or ZlibStart in line or
                ISCStart in line or GPLStart in line):
                if in_license:
                    print('Error: duplicate license start, file:', file,
                            'line:', lineno)
                    exit(1)
                in_license = lineno
            if (BSDEnd in line or ZlibEnd in line or ISCEnd in line or
                GPLEnd in line):
                lic = identify_license(text, file)
                if lic:
                    props['licenses'].add(lic)
                    props['lic_start_end'][lic] = [in_license, lineno]
                in_license = 0
                text = []

            if in_license:
                text.append(line)

            if Apache2 in line:
                props['licenses'].add('Apache-2.0')
            if ZlibRef in line:
                props['licenses'].add('Zlib')
            if MIT in line:
                props['licenses'].add('MIT')
            if ISC in line:
                props['licenses'].add('ISC')

            match = re.search(SPDX_ID, line)
            if match:
                id = match.group('SPDX_ID').strip()
                if props['SPDX_ID']:
                    print('Error: multiple SPDX-License-Identifier tag, file:',
                          file)
                    exit(1)
                props['SPDX_ID'] = id

            if AllRightsReserved in line:
                props['arr'] = True

    if in_license:
        print('Error: end of license text not found, file: ', file)

    props['pureLinaroCopyright'] = (hasLinaroCopyright and not
                                    hasOtherCopyright)
 
    return props


def print_file_and_props(file, props, show_licenses = False, show_spdx = False):
    print(file, end='')
    if show_licenses:
        if props['licenses']:
            for lic in props['licenses']:
                print('', lic, end='')
                if props['lic_start_end'] and props['lic_start_end'].get(lic):
                    first = props['lic_start_end'][lic][0]
                    last = props['lic_start_end'][lic][1]
                    print(' ({:d}-{:d})'.format(first, last), end='')
        else:
            print(' NONE', end='')
    if show_spdx:
        if props['SPDX_ID']:
            print('', '[' + props['SPDX_ID'] + ']', end='')
        else:
            print(' [NONE]', end='')
    print('')


def is_blank(line, props):
    prefix = props['commentPrefix']
    return not line[len(prefix):].strip()


def is_license_line(lineno, line, props):
    skip = False

    for lic in props['licenses']:
        if props['lic_start_end'] and props['lic_start_end'][lic]:
            first = props['lic_start_end'][lic][0]
            last = props['lic_start_end'][lic][1]
            if int(first) <= lineno <= int(last):
                skip = True
            if int(first) - 1 == lineno and is_blank(line, props):
                skip = True
    return skip


def spdx_expr(props):
    n = len(props['licenses'])
    op = props['operator']
    tag = ''

    if  n == 1:
        tag = list(props['licenses'])[0]
    else:
        tag = '(' + op.join(sorted(props['licenses'])) + ')'
    return tag


def insert_spdx(out, props):
    modified = False

    if props['SPDX_ID']:
        return False

    if props['licenses']:
        expr = spdx_expr(props)
        out.write(props['comment_prefix_for_SPDX'] +
                    'SPDX-License-Identifier: ' + expr + '\n')
        modified = True
        if props['has_dual_license'] and len(props['licenses']) > 2:
            print('Warning: please check if operator is correct:', tag,
                  'file:', props[file])

    return modified

def generate_new(file, props):
    if not (args.strip_arr or args.strip_license_text or args.add_spdx):
        return

    newfile = file + '.new'
    modified = False
    lineno = 0
    with open(newfile, 'w') as out:
        with open(file) as f:
            for line in f:
                lineno = lineno + 1
                if args.strip_arr and AllRightsReserved in line:
                    modified = True
                    continue
                if args.strip_license_text and is_license_line(lineno, line, props):
                    modified = True
                    continue
                if args.add_spdx and lineno == props['spdx_insertion']:
                    modified = insert_spdx(out, props)
                out.write(line) 
        if modified:
            mode = os.stat(file).st_mode
            os.rename(newfile, file)
            os.chmod(file, mode)
        else:
            os.remove(newfile)

# (A AND B) -> [A, B]
# (A OR B) -> [A, B]
def parse_spdx_expr(expr):
    s = expr.replace('(', '')
    s = s.replace(')', '')
    s = s.replace('AND', '')
    s = s.replace('OR', '')

    return s.split()

def process(file):
    if not os.path.isfile(file):
        return
    for ext in ignore:
        if file.endswith(ext):
            return
    props = file_props(file)
    if args.linaro_only and not props['pureLinaroCopyright']:
        return
    if (args.unlicensed_only and (props['licenses'] or
            props['SPDX_ID'])):
        return
    if args.mistagged_only:
        mistagged = False
        for lic in props['licenses']:
            if lic not in parse_spdx_expr(props['SPDX_ID']):
                mistagged = True
        if not mistagged:
            return
    if args.arr_only and not props['arr']:
        return
    if args.full_license_only and not props['licenses']:
        return
    print_file_and_props(file, props, args.show, args.show)

    generate_new(file, props)

def main():
    global args
    global ignore

    ignore = ['.new', '.png', '.odg', '.checkpatch', '.txt', '.doc', '.html',
                '.dot', '.svg', '.msc', '.xml', '.md', 'LICENSE', '.license',
                '.pem', '.orig', '.patch', '.xsl', '.a', '.rst' ]

    parser = argparse.ArgumentParser(description='Analyze or modify the '
                                     'license and copyright headers found in '
                                     'source files.')
    parser.add_argument('--show', action='store_true',
                        help='for each source file, print the SPDX ID(s) and '
                         ' line numbers for '
                         'license text found in the file (or NONE), '
                         'followed in square brackets by the SPDX IDs found '
                         'in the file (or [NONE]).')
    muexcl = parser.add_mutually_exclusive_group()
    muexcl.add_argument('--mistagged-only', action='store_true',
                        help='show only file that are mis-tagged, i.e., have some '
                        'license text not reflected by an SPDX IDs.')
    muexcl.add_argument('--unlicensed-only', action='store_true',
                        help='show only source files for which --show would '
                        'print \'NONE [NONE]\', that is, contain no known '
                        'license information at all.')
    parser.add_argument('--full-license-only', action='store_true',
                        help='show only files that have at least one full '
                        'license text block')
    parser.add_argument('--linaro-only', action='store_true',
                        help='show only files that are entirely covered by a '
                        'Linaro copyright')
    parser.add_argument('--arr-only', action='store_true',
                        help='show only files that contain the \'All rights '
                        'reserved\' mention.')
    parser.add_argument('--strip-arr', action='store_true',
                        help='generate .new files without the ARR mention.')
    parser.add_argument('--strip-license-text', action='store_true',
                        help='generate .new files without license text.')
    parser.add_argument('--add-spdx', action='store_true',
                        help='add SPDX identifier(s) to .new files.')
    parser.add_argument('root', nargs=1, 
                        help='the source tree root. All files under this '
                        'root that are considered \'source files\' will be '
                        'processed. The default action consists in printing '
                        'the file path. Use options to display additional '
                        'information or filter out some files. Source files '
                        'are regular files that do not end in: ' + 
                        ' '.join(ignore) + '.')
    args = parser.parse_args()
    if args.show:
        args.show_licenses = True
        args.show_spdx = True
    files = []
    if os.path.isfile(args.root[0]):
        files.append(args.root[0])
    else:
        files = glob.glob(args.root[0] + '/**/*', recursive=True)
    for file in files:
        process(file)

if __name__ == "__main__":
    main()
