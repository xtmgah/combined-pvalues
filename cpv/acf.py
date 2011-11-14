"""
   calculate the autocorrelation of a *sorted* bed file with a set
   of *distance* lags.
"""
import argparse
from array import array
from chart import chart
import sys
import numpy as np
from itertools import groupby, izip
from operator import itemgetter
from _common import bediter, pairwise, get_col_num

def create_acf_list(lags):
    acfs = []
    for lag_min, lag_max in pairwise(lags):
        acfs.append((lag_min, lag_max,
            # array uses less memory than list.
            {"x": array("f"), "y": array("f") }))
    acfs.reverse()
    return acfs

def _acf_by_chrom(args):
    """
    calculate the ACF for a single chromosome
    chromlist is the data for a single chromsome
    """
    chromlist, lags = args
    acfs = create_acf_list(lags)
    if not isinstance(chromlist, list):
        chromlist = list(chromlist)
    max_lag = max(a[1] for a in acfs)
    for ix, xbed in enumerate(chromlist):
        # find all lines within lag of xbed.
        for iy in xrange(ix + 1, len(chromlist)):
            ybed = chromlist[iy]
            # y is always > x so dist calc is simplified.
            dist = ybed['start'] - xbed['end']
            if dist > max_lag: break

            for lag_min, lag_max, xys in acfs:
                # can break out of loop because we reverse-sorted acfs
                # above. this is partial, but we merge below if needed.
                if lag_min <= dist < lag_max:
                    xys["x"].append(xbed['p'])
                    xys["y"].append(ybed['p'])
                elif dist > lag_max:
                    break
    return acfs

def merge_acfs(unmerged):
    """
    utitlity function to merge the chromosomes after
    they've been calculated, and before the correlation
    is calculated.
    """
    merged = unmerged.pop()
    for um in unmerged:
        # have to merge at each lag.
        for (glag_min, glag_max, gxys), (ulag_min, ulag_max, uxys) in \
                                                        izip(merged, um):
            assert glag_min == ulag_min and glag_max == ulag_max
            gxys["x"].extend(uxys["x"])
            gxys["y"].extend(uxys["y"])
            # reduce copies in memory.
            uxys = {}
    return merged

def local_acf(bed_file, lags, col_num0):
    from slk import walk
    # walk yields a tuple of center row, [neighbors]
    # we'll calculate the ACF on neighbors
    max_lag = max(lags)
    lag_str = "\t".join(("%i-%i-corr\t%i-%i-N" % (lmin, lmax, lmin, lmax)) for lmin, lmax in pairwise(lags))
    print "\t".join("#chrom start end p".split()) + "\t" + lag_str

    for key, chromgroup in groupby(bediter(bed_file, col_num0),
                                   itemgetter("chrom")):
        for xbed, neighbors in walk(chromgroup, max_lag):

            line = [xbed['chrom'], str(xbed['start']), str(xbed['end']), "%.4g" % (xbed['p'])]
            for lag_min, lag_max, xys in reversed(_acf_by_chrom((neighbors, lags))):
                xs, ys = xys['x'], xys['y']
                if len(xs) > 3:
                    xs, ys = -np.log10(xs), -np.log10(ys)
                    line.append("%.4g\t%i" % (np.corrcoef(xs, ys)[0, 1],
                                                len(xs)))
                else:
                    line.append("NA\t%i" % len(xs))
            print "\t".join(line)


def acf(fnames, lags, col_num0, partial=True, simple=False):
    """
    calculate the correlation of the numbers in `col_num0` from the bed files
    in `fnames` at various lags. The lags are specified by distance. Partial
    autocorrelation may be calculated as well.

    Since the bed files may be very large, this attempts to be as memory
    efficient as possible while still being very fast for a pure python
    implementation.
    """
    # reversing allows optimization below.
    try:
        from multiprocessing import Pool
        p = Pool()
        imap = p.imap
    except ImportError:
        from itertools import imap

    unmerged_acfs = [] # separated by chrom. need to merge later.
    for fname in fnames:
        # groupby chromosome.
        arg_list = ((list(chromlist), lags) for chrom, chromlist in
                groupby(bediter(fname, col_num0), lambda a: a["chrom"]))

        for chrom_acf in imap(_acf_by_chrom, arg_list):
            unmerged_acfs.append(chrom_acf)

    acfs = merge_acfs(unmerged_acfs)
    acf_res = {}
    xs = np.array([], dtype='f')
    ys = np.array([], dtype='f')
    # iterate over it backwards and remove to reduce memory.
    while len(acfs):
        lmin, lmax, xys = acfs.pop()
        if partial:
            xs, ys = np.array(xys["x"]), np.array(xys["y"])
        else:
            # add the inner layers as we move out.
            xs = np.hstack((xs, xys["x"]))
            ys = np.hstack((ys, xys["y"]))
        if len(xs) == 0:
            print >>sys.stderr, "no values found at lag: %i-%i. skipping" \
                    % (lmin, lmax)
            continue
        if simple:
            acf_res[(lmin, lmax)] = np.corrcoef(xs, ys)[0, 1]
        else:
            acf_res[(lmin, lmax)] = (np.corrcoef(xs, ys)[0, 1], len(xs))
    return sorted(acf_res.items())

def run(args):
    """
    general function that takes an args object (from argparse)
    with the necessary options and calls acf()
    """
    d = map(int, args.d.split(":"))
    d[1] += 1 # adjust for non-inclusive end-points...
    assert len(d) == 3
    lags = range(*d)
    if args.local:
        return local_acf(args.files[0], lags, get_col_num(args.c))

    acf_vals = acf(args.files, lags, get_col_num(args.c), partial=(not
                                                            args.full))
    write_acf(acf_vals, sys.stdout)

def write_acf(acf_vals, out):
    # write acf to a file and return only [((lag_min, lag_max), corr)...]
    simple_acf = []
    values = [float(v[0]) for k, v in acf_vals]
    xlabels = "|".join("%s-%s" % k for k, v in acf_vals)
    print >>out, "#", chart(values, xlabels)
    print >> out, "#lag_min\tlag_max\tcorrelation\tN"
    for k,v in sorted(acf_vals):
        print >> out, "%i\t%i\t%.4g\t%i" % (k[0], k[1], v[0], v[1])
        simple_acf.append((k, v[0]))
    return simple_acf

def main():
    p = argparse.ArgumentParser(description=__doc__,
                   formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("-d", dest="d", help="start:stop:stepsize of distance. e.g."
            " %(default)s means check acf at distances of:"
            "[15, 65, 115, 165, 215, 265, 315, 365, 415, 465]",
            type=str, default="15:500:50")
    p.add_argument("-c", dest="c", help="column number that has the value to"
                   "take the  acf", type=int, default=4)
    p.add_argument("--full", dest="full", action="store_true",
                   default=False, help="do full autocorrelation (default"
                   " is partial")
    p.add_argument("--local", dest="local", action="store_true",
                   default=False, help="do local ACF")
    p.add_argument('files', nargs='+', help='files to process')
    args = p.parse_args()
    if (len(args.files) == 0):
        sys.exit(not p.print_help())
    return run(args)

if __name__ == "__main__":
    import doctest
    if doctest.testmod(optionflags=doctest.ELLIPSIS |\
                                   doctest.NORMALIZE_WHITESPACE).failed == 0:
        main()
