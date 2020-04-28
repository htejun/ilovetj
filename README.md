# ilovetj

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

  It's working if the generated png file contains "test".

All pages are converted to bitmaps and processed with ImageMagick. All pages
in the output pdf are bitmaps. The resolution can be changed with --dpi.
To install ImageMagick, visit https://imagemagick.org.

EXAMPLE:

  Let's say the SPECS directory contains the following files.

    L1-VENDOR1-FIXTURE1.pdf
    D1-VENDOR2-FIXTURE1.pdf
    L2-VENDOR2-FIXTURE2.pdf

  The following command will create output.pdf which contains all pages of
  the three pdfs in the order D1, L1 and L2. Each page of the output will
  have header and footer attached and labeled with D1, L1 or L2 on the
  bottom right.

    $ ilovetj.py -l "-" -o output.pdf SPECS

This program was written after being horribly shocked at Seulki Kim
<seulki@looplighthing.nyc> spending literally hours copy-pasting pdf pages
into InDesign and manually labeling them.
