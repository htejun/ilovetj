#!/usr/bin/env python3
#
# Copyright (C) 2020 Tejun Heo <tj@kernel.org>
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# 
import argparse
import os
import sys
import platform
import atexit
import shutil
import subprocess
import glob
import re
import tempfile
import threading
import time

desc = '''
Annotate pages from source pdfs and collect them into a single pdf.

* If directories are specified, all .pdf files within are used as input. The
  input pdfs are sorted in combined alphanumeric order after segmenting the
  names on whitespaces, '-', and '-'. Sorting can be disabled with
  --keep-order.

* If specified, header and footer images are attached to each page. The
  source page is shrunk to fit. The heights of the header and footer are
  specified in percents of the total page height.

* If label separator is specified, the portion of the filename before the
  separator is used to annotate each page. For example, if --label-sep is
  set to '-' and an input filename is L1-VENDOR-FIXTURE.pdf, each page of
  the pdf will be labeled with "L1".

* Label font, size and color can be changed. Note that not all fonts listed
  in "convert -list font" work. You can test which fonts work with the
  following command.

    convert -font "FONT_NAME" -size 128x128 label:test test.png

  It's working if the generated png file contains "test". On windows, prefix
  all ImageMagick commands with magick - "magick convert" instead of
  "convert".

All pages are converted to bitmaps with ghostscript and processed with
ImageMagick. All pages in the output pdf are bitmaps. The resolution can be
changed with --dpi. To install ghostscript and ImageMagick, visit
https://www.ghostscript.com and https://imagemagick.org.

EXAMPLE:

  Let's say the SPECS directory contains the following files.

    L1-VENDOR1-FIXTURE1.pdf
    D1-VENDOR2-FIXTURE1.pdf
    L2-VENDOR2-FIXTURE2.pdf

  The following command will create output.pdf which contains all pages of
  the three pdfs in the order D1, L1 and L2. Each page of the output will
  have header and footer attached and labeled with D1, L1 or L2 on the
  bottom right.

    $ ilovetj.py --label-sep "-" -o output.pdf SPECS

This program was written after being horribly shocked at Seulki Kim
<seulki@looplighthing.nyc> spending literally hours copy-pasting pdf pages
into InDesign and manually labeling them.
'''

GRAVITY_CHOICES=['northwest', 'north', 'northeast',
                 'west', 'center', 'east',
                 'southwest', 'south', 'southeast']

parser = argparse.ArgumentParser(description=desc,
                                 formatter_class=argparse.RawTextHelpFormatter)
parser.add_argument('src', metavar='PDF_OR_DIR', nargs='+',
                    help='Source PDF files or directories')
parser.add_argument('--output', '-o', required=True,
                    help='Output pdf file')
parser.add_argument('--numbered-output', metavar='true|false', type=bool,
                    default=platform.system() == 'Windows',
                    help='Append number to output filename to avoid overwriting (default: %(default)s)')
parser.add_argument('--dpi', '-d', metavar='DPI', type=int, default=300,
                    help='Processing DPI (default: %(default)s)')
parser.add_argument('--keep-order', action='store_true',
                    help='Keep source PDF order instead of sorting them alphabetically')
parser.add_argument('--size', metavar='WIDTHxHEIGHT', default='215.9x279.4',
                    help='paper size in millimeters (default: %(default)s)')

parser.add_argument('--header', metavar='IMAGE',
                    help='header image to use')
parser.add_argument('--header-height', metavar='PCT', type=float, default=10,
                    help='header height in percents of the page height (default: %(default)s)')
parser.add_argument('--footer', metavar='IMAGE',
                    help='footer image to use')
parser.add_argument('--footer-height', metavar='PCT', type=float, default=20,
                    help='footer height in percents of the page height (default: %(default)s)')

parser.add_argument('--label-sep', metavar='SEPARATOR',
                    help='filename label separator')
parser.add_argument('--label-height', metavar='PCT', type=float, default=4,
                    help='label height in percents of the page height (default: %(default)s)')
parser.add_argument('--label-gravity', metavar='GRAVITY',
                    choices=GRAVITY_CHOICES, default='southeast',
                    help='label position orientation (default: %(default)s)')
parser.add_argument('--label-margin', metavar='XPCTxYPCT', default='4.5x8',
                    help='margin around label in percents of page size (default: %(default)s)')
parser.add_argument('--label-color', metavar='COLOR', default='red',
                    help='label color (default: %(default)s)')
parser.add_argument('--label-font', metavar='FONT',
                    help='label font, "convert -list font" to see the font list')

parser.add_argument('--number-start', metavar='START',
                    help='Number to start numbering pages from')
parser.add_argument('--number-height', metavar='PCT', type=float, default=2,
                    help='page number height in percents of the page height (default: %(default)s)')
parser.add_argument('--number-gravity', metavar='GRAVITY',
                    choices=GRAVITY_CHOICES, default='southeast',
                    help='page number position orientation (default: %(default)s)')
parser.add_argument('--number-margin', metavar='XPCTxYPCT', default='4.5x5',
                    help='margin around page number in percents of page size (default: %(default)s)')
parser.add_argument('--number-color', metavar='COLOR', default='black',
                    help='page number color (default: %(default)s)')
parser.add_argument('--number-font', metavar='FONT',
                    help='page number font, "convert -list font" to see the font list')

parser.add_argument('--concurrency', type=int, default=os.cpu_count(),
                    help='maximum concurrency (default: %(default)s)')
parser.add_argument('--verbose', '-v', action='count', default = 0)
parser.add_argument('--tempdir', metavar='DIR',
                    help='specify explicit temporary directory for debugging')

def is_windows():
    return platform.system() == 'Windows'

def err(msg):
    print(msg, file=sys.stderr)
    sys.stderr.flush()
    sys.exit(1)

def info(msg):
    if prog_args.verbose >= 0:
        print(msg, file=sys.stderr)
        sys.stderr.flush()

def dbg(msg):
    if prog_args.verbose > 0:
        print(msg, file=sys.stderr)
        sys.stderr.flush()

def ddbg(msg):
    if prog_args.verbose > 1:
        print(msg, file=sys.stderr)
        sys.stderr.flush()

def sectioned_mixed_key(x):
    sections = re.split('-|_|\W', x)
    keys = []
    for s in sections:
        for k in re.split('([0-9]+)', s):
            if len(k) == 0:
                continue
            if k.isdigit():
                if len(keys) > 0 and isinstance(keys[-1], int):
                    keys.append("")
                keys.append(int(k))
            else:
                keys.append(k)
        if len(keys) > 0 and not isinstance(keys[-1], int):
            keys.append(-1)
    ddbg(f'section_mixed_key: {x} -> {keys}')
    return keys

def stem_name(path):
    return os.path.splitext(os.path.basename(path))[0]

def sorted_mixed_basename(l):
    return sorted(l, key = lambda key: sectioned_mixed_key(stem_name(key)))

def find_bin(cmd, win_glob=None):
    bin_path = shutil.which(cmd)
    if bin_path is not None:
        return bin_path
    if win_glob is None or not is_windows():
        return None
    cands = glob.glob(win_glob)
    if len(cands) > 0:
        return cands[0]
    return None

def find_magick_bin(cmd, win_glob=None):
    bin_path = find_bin(cmd, win_glob)
    if bin_path is None:
        return None
    if b'ImageMagick' not in subprocess.check_output([bin_path, '-version']):
        return None
    return bin_path

def run_gs(args):
    cmd = [GS_BIN]
    cmd += args
    dbg(f'Running {cmd}')
    try:
        subprocess.check_call(cmd)
    except Exception as e:
        err(f'ghostscript command ({cmd}) failed ({e})')

def run_convert(args):
    if MAGICK_BIN is None:
        cmd = [CONVERT_BIN]
    else:
        cmd = [MAGICK_BIN, 'convert']
    cmd += args
    dbg(f'Running {cmd}')
    try:
        subprocess.check_call(cmd)
    except Exception as e:
        err(f'convert command ({cmd}) failed ({e})')

def run_composite(args):
    if MAGICK_BIN is None:
        cmd = [COMPOSITE_BIN]
    else:
        cmd = [MAGICK_BIN, 'composite']
    cmd += args
    dbg(f'Running {cmd}')
    try:
        subprocess.check_call(cmd)
    except Exception as e:
        err(f'composite command ({cmd}) failed ({e})')

def resize_header(src, dst, size):
    info(f'Resizing {src} to {size[0]}x{size[1]}')
    run_convert([src,
                 '(', '-resize', f'{size[0]}x{size[1]}', ')',
                 '(', '-gravity', 'West', '-extent', f'{size[0]}x{size[1]}', ')',
                 dst])

def run_parallel(args_set, runfn, max_active):
    dbg(f'run_parallel: args_set={args_set}')

    nr_active = 0
    threads = []
    for arg in args_set:
        nr_active += 1

        def run_and_dec(arg):
            nonlocal nr_active
            runfn(arg)
            nr_active -= 1

        t = threading.Thread(target=lambda: run_and_dec(arg))
        t.start()

        threads.append(t)
        while nr_active >= max_active:
            time.sleep(0.1)

    for t in threads:
        t.join()

def generate_label_args(label, font, height, color, gravity, filename):
    args = []
    if font is not None:
        args += [ '-font', font ]
    elif platform.system() == 'Linux':
        args += [ '-font', 'Bitstream-Vera-Sans-Bold' ]

    args += [ '-background', 'none',
              '-fill', color,
              '-size', f'{size[0]}x{height}',
              '-gravity', GRAVITY_X[gravity],
              f'label:{label}',
              filename ]
    return args

def apply_labels(srcs, label_files, prefix, report_prefix,
                 height, gravity, margin):
    labeled = []
    args_set = []
    for src in srcs:
        stem = os.path.splitext(src.split('_', 1)[1])[0]
        dst = f'{prefix}_{stem}.png'

        src_file = f'{tempdir}/{src}'
        dst_file = f'{tempdir}/{dst}'

        args = [f'{report_prefix} "{stem}"']
        args += [ '-gravity', GRAVITY_Y[gravity],
                  '-geometry', f'-{margin[0]}+{margin[1]}',
                  label_files[src],
                  f'{tempdir}/{src}',
                  f'{tempdir}/{dst}' ]

        args_set.append(args)
        labeled.append(dst)

    def apply_label_fn(args):
        info(f'{args[0]}...')
        run_composite(args[1:])

    run_parallel(args_set, apply_label_fn, prog_args.concurrency)
    return labeled

# main starts here
MM_PER_IN = 25.4

GRAVITY_X = { 'northwest' : 'west',
              'north'     : 'center',
              'northeast' : 'east',
              'west'      : 'west',
              'center'    : 'center',
              'east'      : 'east',
              'southwest' : 'west',
              'south'     : 'center',
              'southeast' : 'east' }

GRAVITY_Y = { 'northwest' : 'north',
              'north'     : 'north',
              'northeast' : 'north',
              'west'      : 'center',
              'center'    : 'center',
              'east'      : 'center',
              'southwest' : 'south',
              'south'     : 'south',
              'southeast' : 'south' }

if is_windows():
    atexit.register(lambda: os.system('pause'))

prog_args = parser.parse_args()

GS_BIN = find_bin('gs', 'C:/Program Files/gs/gs*/bin/gswin*c.EXE')
if GS_BIN is None:
    err(f'Ghostscript is not found. Please install from https://www.ghostscript.com/')

MAGICK_BIN = find_magick_bin('magick', 'C:/Program Files/ImageMagick*/magick.EXE')
if MAGICK_BIN is None:
    CONVERT_BIN = find_magick_bin('convert')
    COMPOSITE_BIN = find_magick_bin('composite')
    if CONVERT_BIN is None or COMPOSITE_BIN is None:
        err(f'ImageMagick is not found. Please install from https://imagemagick.org')
else:
    CONVERT_BIN = None
    COMPOSITE_BIN = None

if not 'ilovetj' in sys.argv[0]:
    err(f'Command name {sys.argv[0]} does not contain "ilovetj"')

# parse paper size
try:
    paper_size = prog_args.size.split('x', 2)
    paper_size = (float(paper_size[0]), float(paper_size[1]))
    if paper_size[0] <= 0 or paper_size[1] <= 0:
        raise Exception('must be positive')
except Exception as e:
    err(f'--size must be in the format WIDTHxHEIGHT ({e})')

# convert that to pixel size based on the dpi
size = (int(paper_size[0] / MM_PER_IN * prog_args.dpi),
        int(paper_size[1] / MM_PER_IN * prog_args.dpi))

# parse label margin
try:
    label_margin = prog_args.label_margin.split('x', 2)
    label_margin = (int(size[0] * float(label_margin[0]) / 100),
                    int(size[1] * float(label_margin[1]) / 100))
except Exception as e:
    err(f'--label-margin must be in the format XPCTxYPCT ({e})')

# parse number start
if prog_args.number_start is None:
    number_start = None
else:
    try:
        number_start = int(prog_args.number_start)
    except Exception as e:
        err(f'--number-start must be an integer ({e})')

# parse number margin
try:
    number_margin = prog_args.number_margin.split('x', 2)
    number_margin = (int(size[0] * float(number_margin[0]) / 100),
                     int(size[1] * float(number_margin[1]) / 100))
except Exception as e:
    err(f'--label-margin must be in the format XPCTxYPCT ({e})')

# determine header, content and footer sizes
header_height = 0
footer_height = 0

if prog_args.header is not None:
    header_height = int(size[1] * prog_args.header_height / 100.0)

if prog_args.footer is not None:
    footer_height = int(size[1] * prog_args.footer_height / 100.0)

body_height = size[1] - header_height - footer_height

info(f'paper={paper_size[0]}x{paper_size[1]} pixels={size[0]}x{size[1]} '
     f'header:body:footer={header_height}:{body_height}:{footer_height}')

if header_height < 0 or body_height < 0 or footer_height < 0:
    err('Some heights came out negative')

# determine source files
pdfs = []
for src in prog_args.src:
    if os.path.isdir(src):
        pdfs += sorted_mixed_basename(glob.glob(f'{src}/*.pdf'))
        continue
    elif os.path.isfile(src):
        pdfs.append(src)
        continue
    elif is_windows() and len(src) and src[-1] == '"':
        # When run through windows powershell, quoting somehow can get
        # broken and source may end up with a trailing extra double quote.
        src = src[:-1]
        if os.path.isdir(src):
            pdfs += sorted_mixed_basename(glob.glob(f'{src}/*.pdf'))
            continue
        elif os.path.isfile(src):
            pdfs.append(src)
            continue

    err(f'Invalid source file/dir "{src}"')

if not prog_args.keep_order:
    pdfs = sorted_mixed_basename(pdfs)

# create tempdir
if prog_args.tempdir is None:
    tempdir_obj = tempfile.TemporaryDirectory()
    tempdir = tempdir_obj.name
else:
    tempdir = prog_args.tempdir.rstrip('/')
    os.makedirs(tempdir, exist_ok=True)

dbg(f'pdfs={pdfs} tempdir={tempdir}')

# prepare resized header and footer
header_file = None
footer_file = None

if prog_args.header is not None:
    header_file = f'{tempdir}/__HEADER__.png'
    resize_header(prog_args.header, header_file, (size[0], header_height))
if prog_args.footer is not None:
    footer_file = f'{tempdir}/__FOOTER__.png'
    resize_header(prog_args.footer, footer_file, (size[0], footer_height))

# convert to pngs
srcs = []
args_set = []
for pdf in pdfs:
    stem = stem_name(pdf)
    args = [stem]
    args += [ '-q', '-dQUIET', '-dSAFER', '-dBATCH', '-dNOPAUSE', '-dNOPROMPT',
              '-dMaxBitMap=500000000', '-dAlignToPixels=0', '-dGridFitTT=2',
              '-sDEVICE=png16m', '-dTextAlphaBits=4', '-dGraphicsAlphaBits=4',
              f'-r{prog_args.dpi}',
              f'-sOutputFile={tempdir}/SRC_{stem}-%d.png', pdf ]
    args_set.append(args)
    srcs.append(f'SRC_{stem}.png')

def render_fn(args):
    info(f'Rendering "{args[0]}"...')
    run_gs(args[1:])

run_parallel(args_set, render_fn, prog_args.concurrency)

new_srcs = []
for src in srcs:
    pattern = f'{tempdir}/{os.path.splitext(src)[0]}*.png'
    new_srcs += [ os.path.basename(p) for p in sorted_mixed_basename(glob.glob(pattern)) ]
dbg(f'srcs={srcs} new_srcs={new_srcs}')
srcs = new_srcs

# resize to body_height
resized = []
args_set = []
for src in srcs:
    dst = f'RESIZED_{src.split("_", 1)[1]}'

    args = [stem_name(dst).split('_', 1)[1]]
    args += [f'{tempdir}/{src}',
             '(', '-strip', ')',
             '(', '-resize', f'{size[0]}x{body_height}', ')',
             '(', '-gravity', 'center', '-extent', f'{size[0]}x{body_height}', ')',
             f'{tempdir}/{dst}']

    args_set.append(args)
    resized.append(dst)

def resize_fn(args):
    info(f'Resizing "{args[0]}"...')
    run_convert(args[1:])

run_parallel(args_set, resize_fn, prog_args.concurrency)
srcs = resized

# merge header and footer
if header_file is not None or footer_file is not None:
    merged = []
    args_set = []
    for src in srcs:
        dst = f'MERGED_{src.split("_", 1)[1]}'
        src_file = f'{tempdir}/{src}'
        dst_file = f'{tempdir}/{dst}'

        args = [stem_name(dst).split('_', 1)[1]]
        args += [ '-append' ]
        if header_file is not None:
            args.append(header_file)
        args.append(src_file)
        if footer_file is not None:
            args.append(footer_file)
        args.append('-strip')
        args.append(dst_file)

        args_set.append(args)
        merged.append(dst)

    def merge_fn(args):
        info(f'Merging "{args[0]}"...')
        run_convert(args[1:])

    run_parallel(args_set, merge_fn, prog_args.concurrency)
    srcs = merged

# label
if prog_args.label_sep is not None:
    # generate labels
    label_height = int(size[1] * prog_args.label_height / 100)
    labels = set()
    label_files = {}
    args_set = []
    for src in srcs:
        stem = os.path.splitext(src.split('_', 1)[1])[0]
        label = stem.split(prog_args.label_sep, 1)[0]
        label_file = f'{tempdir}/LABEL_{label}.png'
        label_files[src] = label_file

        if label in labels:
            continue
        labels.add(label)

        args = [label]
        args += generate_label_args(label, prog_args.label_font,
                                    label_height, prog_args.label_color,
                                    prog_args.label_gravity, label_file)
        args_set.append(args)

    def label_fn(args):
        info(f'Generating label "{args[0]}"...')
        run_convert(args[1:])

    run_parallel(args_set, label_fn, prog_args.concurrency)

    dbg(f'label_files={label_files}')

    # apply labels
    srcs = apply_labels(srcs, label_files, 'LABELED', 'Labeling',
                        label_height, prog_args.label_gravity, label_margin)

# number
if number_start is not None:
    # generate numbers
    number_height = int(size[1] * prog_args.number_height / 100)
    number = number_start
    number_files = {}
    args_set = []
    for src in srcs:
        stem = os.path.splitext(src.split('_', 1)[1])[0]
        number_file = f'{tempdir}/NUMBER_{number}.png'
        number_files[src] = number_file

        args = [number]
        args += generate_label_args(f'{number}', prog_args.number_font,
                                    number_height, prog_args.number_color,
                                    prog_args.number_gravity, number_file)
        args_set.append(args)
        number += 1

    def number_fn(args):
        info(f'Generating page number "{args[0]}"...')
        run_convert(args[1:])

    run_parallel(args_set, number_fn, prog_args.concurrency)

    dbg(f'number_files={number_files}')

    # apply numbers
    srcs = apply_labels(srcs, number_files, 'NUMBERED', 'Numbering',
                        number_height, prog_args.number_gravity, number_margin)

# collect the processed results into the output pdf
output_path = prog_args.output
if prog_args.numbered_output and os.path.exists(output_path):
    (base, ext) = os.path.splitext(output_path)
    nr = 1
    while True:
        output_path = f'{base}.{nr}{ext}'
        if not os.path.exists(output_path):
            break
        nr += 1

args = [ '-format', 'pdf',
         '-resize', f'{size[0]}x{size[1]}',
         '-units', 'PixelsPerInch',
         '-density', f'{prog_args.dpi}' ]
args += [ f'{tempdir}/{src}' for src in srcs ]
args.append(output_path)

info(f'Collecting annotated pages into "{output_path}"...')
run_convert(args)
info('Done')
