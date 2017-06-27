#!/usr/bin/python
# -*- coding: utf-8 -*-

# ===--- compare_perf_tests.py -------------------------------------------===//
#
#  This source file is part of the Swift.org open source project
#
#  Copyright (c) 2014 - 2017 Apple Inc. and the Swift project authors
#  Licensed under Apache License v2.0 with Runtime Library Exception
#
#  See https://swift.org/LICENSE.txt for license information
#  See https://swift.org/CONTRIBUTORS.txt for the list of Swift project authors
#
# ===---------------------------------------------------------------------===//

from __future__ import print_function

import argparse
import re
import sys
from math import sqrt


class PerformanceTestResult(object):
    """PerformanceTestResult holds results from executing an individual
    benchmark from the Swift Benchmark Suite as reported by the test driver
    (Benchmark_O, Benchmark_Onone, Benchmark_Ounchecked or Benchmark_Driver).

    It depends on the log format emitted by the test driver in the form:
    #,TEST,SAMPLES,MIN(μs),MAX(μs),MEAN(μs),SD(μs),MEDIAN(μs),MAX_RSS(B)

    The last column, MAX_RSS, is emitted only for runs instrumented by the
    Benchmark_Driver to measure rough memory use during the execution of the
    benchmark.
    """
    def __init__(self, csv_row):
        """PerformanceTestResult instance is created from an iterable with
        length of 8 or 9. (Like a row provided by the CSV parser.)
        """
        # csv_row[0] is just an ordinal number of the test - skip that
        self.name = csv_row[1]          # Name of the performance test
        self.samples = int(csv_row[2])  # Number of measurement samples taken
        self.min = int(csv_row[3])      # Minimum runtime (ms)
        self.max = int(csv_row[4])      # Maximum runtime (ms)
        self.mean = int(csv_row[5])     # Mean (average) runtime (ms)
        sd = int(csv_row[6])            # Standard Deviation (ms)
        # For computing running variance
        self.S_runtime = (0 if self.samples < 2 else
                          (sd * sd) * (self.samples - 1))
        self.median = int(csv_row[7])   # Median runtime (ms)
        self.max_rss = (                # Maximum Resident Set Size (B)
            int(csv_row[8]) if len(csv_row) > 8 else None)
        # Sample lists for statistical analysis of measured results
        self.all_samples = None

    def __repr__(self):
        return (
            '<PerformanceTestResult name:{0.name!r} '
            'samples:{0.samples!r} min:{0.min!r} max:{0.max!r} '
            'mean:{0.mean!r} sd:{0.sd!r} median:{0.median!r}>'.format(self))

    @property
    def sd(self):
        """Standard Deviation (ms)"""
        return (0 if self.samples < 2 else
                sqrt(self.S_runtime / (self.samples - 1)))

    @staticmethod
    def running_mean_variance((k, M_, S_), x):
        """
        Compute running variance, B. P. Welford's method
        See Knuth TAOCP vol 2, 3rd edition, page 232, or
        https://www.johndcook.com/blog/standard_deviation/
        M is mean, Standard Deviation is defined as sqrt(S/k-1)
        """
        k = float(k + 1)
        M = M_ + (x - M_) / k
        S = S_ + (x - M_) * (x - M)
        return (k, M, S)

    def merge(self, r):
        """Merging test results recomputes min and max.
        It attempts to recompute mean and standard deviation when all_samples
        are available. There is no correct way to compute these values from
        test results that are summaries from more than 3 samples.

        The use case here is comparing tests results parsed from concatenated
        log files from multiple runs of benchmark driver.
        """
        self.min = min(self.min, r.min)
        self.max = max(self.max, r.max)
        # self.median = None # unclear what to do here

        def push(x):
            state = (self.samples, self.mean, self.S_runtime)
            state = self.running_mean_variance(state, x)
            (self.samples, self.mean, self.S_runtime) = state

        # Merging test results with up to 3 samples is exact
        values = [r.min, r.max, r.median][:min(r.samples, 3)]
        map(push, values)


class ResultComparison(object):
    """ResultComparison compares MINs from new and old PerformanceTestResult.
    It computes speedup ratio and improvement delta (%).
    """
    def __init__(self, old, new):
        self.old = old
        self.new = new
        assert(old.name == new.name)
        self.name = old.name  # Test name, convenience accessor

        # Speedup ratio
        self.ratio = (old.min + 0.001) / (new.min + 0.001)

        # Test runtime improvement in %
        ratio = (new.min + 0.001) / (old.min + 0.001)
        self.delta = ((ratio - 1) * 100)

        # Indication of dubious changes: when result's MIN falls inside the
        # (MIN, MAX) interval of result they are being compared with.
        self.is_dubious = ((old.min < new.min and new.min < old.max) or
                           (new.min < old.min and old.min < new.max))


class LogParser(object):
    """LogParser converts variously formatted log outputs from
    `Benchmark_Driver` and `Benchmark_O`('Onone', 'Ounchecked') into
    `PerformanceTestResult`s. It can also merge together the results from
    concatenated log files.
    """
    def __init__(self):
        self.results, self.samples, self.num_iters = [], [], 1

    # Parse lines like this
    # #,TEST,SAMPLES,MIN(μs),MAX(μs),MEAN(μs),SD(μs),MEDIAN(μs)
    results_re = re.compile(r'(\d+[, \t]*\w+[, \t]*' +
                            r'[, \t]*'.join([r'[\d.]+'] * 6) +
                            r'[, \t]*[\d.]*)')  # optional MAX_RSS(B)

    def _append_result(self, result):
        columns = result.split(',')
        if len(columns) < 8:
            columns = result.split()
        r = PerformanceTestResult(columns)
        if self.samples:
            r.all_samples = self.samples
        self.results.append(r)
        self.num_iters, self.samples = 1, []

    # Regular expression and action to take when it matches the parsed line
    state_actions = {
        results_re: _append_result,

        # Adaptively determined N; test loop multiple adjusting runtime to ~1s
        re.compile(r'\s+Measuring with scale (\d+).'):
        (lambda self, num_iters: setattr(self, 'num_iters', num_iters)),

        re.compile(r'\s+Sample (\d+),(\d+)'):
        (lambda self, i, runtime:
         self.samples.append((int(i), int(self.num_iters), int(runtime)))),

        # FIXME remove pre-processed sample format
        # (I have manually reformatted logs for importing into Numbers:)
        re.compile(r'(\d+)\t(\d+)\t(\d+)'):
        (lambda self, i, num_iters, runtime:
         self.samples.append((int(i), int(num_iters), int(runtime))))
    }

    def parse_results(self, lines):
        for line in lines:
            for regexp, action in LogParser.state_actions.items():
                match = regexp.match(line)
                if match:
                    action(self, *match.groups())
                    break  # stop after 1st match
            else:  # If none matches, skip the line.
                # print('skipping: ' + line.rstrip('\n'))
                continue
        return self.results

    @staticmethod
    def _results_from_lines(lines):
        tests = LogParser().parse_results(lines)

        def add_or_merge(names, r):
            if r.name not in names:
                names[r.name] = r
            else:
                names[r.name].merge(r)
            return names

        return reduce(add_or_merge, tests, dict())

    @staticmethod
    def results_from_string(log_contents):
        """Returns dictionary of test names and `PerformanceTestResult`s
        parsed from the supplied string.
        """
        return LogParser._results_from_lines(log_contents.splitlines())

    @staticmethod
    def results_from_file(log_file):
        """Returns dictionary of test names and `PerformanceTestResult`s
        parsed from the log file.
        """
        with open(log_file) as f:
            return LogParser._results_from_lines(f.readlines())


class TestComparator(object):
    """TestComparator parses `PerformanceTestResult`s from CSV log files.
    Then it determines which tests were `added`, `removed` and which can be
    compared. It then splits the `ResultComparison`s into 3 groups according to
    the `delta_threshold` by the change in performance: `increased`,
    `descreased` and `unchanged`.

    The lists of `added`, `removed` and `unchanged` tests are sorted
    alphabetically. The `increased` and `decreased` lists are sorted in
    descending order by the amount of change.
    """
    def __init__(self, old_results, new_results, delta_threshold):
        old_tests = set(old_results.keys())
        new_tests = set(new_results.keys())
        comparable_tests = new_tests.intersection(old_tests)
        added_tests = new_tests.difference(old_tests)
        removed_tests = old_tests.difference(new_tests)

        self.added = sorted([new_results[t] for t in added_tests],
                            key=lambda r: r.name)
        self.removed = sorted([old_results[t] for t in removed_tests],
                              key=lambda r: r.name)

        def compare(name):
            return ResultComparison(old_results[name], new_results[name])

        comparisons = map(compare, comparable_tests)

        def partition(l, p):
            return reduce(lambda x, y: x[not p(y)].append(y) or x, l, ([], []))

        decreased, not_decreased = partition(
            comparisons, lambda c: c.ratio < (1 - delta_threshold))
        increased, unchanged = partition(
            not_decreased, lambda c: c.ratio > (1 + delta_threshold))

        # sorted partitions
        names = [c.name for c in comparisons]
        comparisons = dict(zip(names, comparisons))
        self.decreased = [comparisons[c.name]
                          for c in sorted(decreased, key=lambda c: -c.delta)]
        self.increased = [comparisons[c.name]
                          for c in sorted(increased, key=lambda c: c.delta)]
        self.unchanged = [comparisons[c.name]
                          for c in sorted(unchanged, key=lambda c: c.name)]


class ReportFormatter(object):
    """ReportFormatter formats the `PerformanceTestResult`s and
    `ResultComparison`s provided by `TestComparator` using their `header` and
    `values()` into report table. Supported formats are: `markdown` (used for
    displaying benchmark results on GitHub), `git` and `html`.
    """
    def __init__(self, comparator, old_branch, new_branch, changes_only):
        self.comparator = comparator
        self.old_branch = old_branch
        self.new_branch = new_branch
        self.changes_only = changes_only

    MARKDOWN_DETAIL = """
<details {3}>
  <summary>{0} ({1})</summary>
  {2}
</details>
"""
    GIT_DETAIL = """
{0} ({1}): {2}"""

    PERFORMANCE_TEST_RESULT_HEADER = ('TEST', 'MIN', 'MAX', 'MEAN', 'MAX_RSS')
    RESULT_COMPARISON_HEADER = ('TEST', 'OLD', 'NEW', 'DELTA', 'SPEEDUP')

    @staticmethod
    def header_for(result):
        """Column labels for header row in results table"""
        return (ReportFormatter.PERFORMANCE_TEST_RESULT_HEADER
                if isinstance(result, PerformanceTestResult) else
                # isinstance(result, ResultComparison)
                ReportFormatter.RESULT_COMPARISON_HEADER)

    @staticmethod
    def values(result):
        """Values for display in results table comparisons."""
        return (
            (result.name,
             str(result.min), str(result.max), str(int(result.mean)),
             str(result.max_rss) if result.max_rss else '—')
            if isinstance(result, PerformanceTestResult) else
            # isinstance(result, ResultComparison)
            (result.name,
             str(result.old.min), str(result.new.min),
             '{0:+.1f}%'.format(result.delta),
             '{0:.2f}x{1}'.format(result.ratio,
                                  ' (?)' if result.is_dubious else ''))
        )

    def markdown(self):
        return self._formatted_text(
            ROW='{0} | {1} | {2} | {3} | {4} \n',
            HEADER_SEPARATOR='---',
            DETAIL=self.MARKDOWN_DETAIL)

    def git(self):
        return self._formatted_text(
            ROW='{0}   {1}   {2}   {3}   {4} \n',
            HEADER_SEPARATOR='   ',
            DETAIL=self.GIT_DETAIL)

    def _column_widths(self):
        changed = self.comparator.decreased + self.comparator.increased
        results = (changed if self.changes_only else
                   changed + self.comparator.unchanged)
        results += self.comparator.added + self.comparator.removed

        widths = [
            map(len, columns) for columns in
            [ReportFormatter.PERFORMANCE_TEST_RESULT_HEADER,
             ReportFormatter.RESULT_COMPARISON_HEADER] +
            [ReportFormatter.values(r) for r in results]
        ]

        def max_widths(maximum, widths):
            return tuple(map(max, zip(maximum, widths)))

        return reduce(max_widths, widths, tuple([0] * 5))

    def _formatted_text(self, ROW, HEADER_SEPARATOR, DETAIL):
        widths = self._column_widths()

        def justify_columns(contents):
            return tuple([c.ljust(w) for w, c in zip(widths, contents)])

        def row(contents):
            return ROW.format(*justify_columns(contents))

        def header(header):
            return '\n' + row(header) + row(tuple([HEADER_SEPARATOR] * 5))

        def format_columns(r, strong):
            return (r if not strong else
                    r[:-1] + ('**{0}**'.format(r[-1]), ))

        def table(title, results, is_strong=False, is_open=False):
            rows = [
                row(format_columns(ReportFormatter.values(r), is_strong))
                for r in results
            ]
            return ('' if not rows else
                    DETAIL.format(*[
                        title, len(results),
                        (header(ReportFormatter.header_for(results[0])) +
                         ''.join(rows)),
                        ('open' if is_open else '')
                    ]))

        return ''.join([
            # FIXME print self.old_branch, self.new_branch
            table('Regression', self.comparator.decreased, True, True),
            table('Improvement', self.comparator.increased, True),
            ('' if self.changes_only else
             table('No Changes', self.comparator.unchanged)),
            table('Added', self.comparator.added, is_open=True),
            table('Removed', self.comparator.removed, is_open=True)
        ])

    HTML = """
<!DOCTYPE html>
<html>
<head>
    <meta http-equiv="Content-Type" content="text/html; charset=utf-8" />
    <style>
        body {{ font-family: -apple-system, sans-serif; font-size: 14px; }}
        table {{ border-spacing: 2px; border-color: gray; border-spacing: 0;
                border-collapse: collapse; }}
        table tr {{ background-color: #fff; border-top: 1px solid #c6cbd1; }}
        table th, table td {{ padding: 6px 13px; border: 1px solid #dfe2e5; }}
        th {{ text-align: center; padding-top: 130px; }}
        td {{ text-align: right; }}
        table td:first-child {{ text-align: left; }}
        tr:nth-child(even) {{ background-color: #000000; }}
        tr:nth-child(2n) {{ background-color: #f6f8fa; }}
    </style>
</head>
<body>
<table>
{0}
</table>
</body>
</html>"""

    HTML_HEADER_ROW = """
        <tr>
                <th align='left'>{0} ({1})</th>
                <th align='left'>{2}</th>
                <th align='left'>{3}</th>
                <th align='left'>{4}</th>
                <th align='left'>{5}</th>
        </tr>
"""

    HTML_ROW = """
        <tr>
                <td align='left'>{0}</td>
                <td align='left'>{1}</td>
                <td align='left'>{2}</td>
                <td align='left'>{3}</td>
                <td align='left'><font color='{4}'>{5}</font></td>
        </tr>
"""

    def html(self):

        def row(name, old, new, delta, speedup, speedup_color):
            return self.HTML_ROW.format(
                name, old, new, delta, speedup_color, speedup)

        def header(contents):
            return self.HTML_HEADER_ROW.format(* contents)

        def table(title, results, speedup_color):
            rows = [
                row(*(ReportFormatter.values(r) + (speedup_color,)))
                for r in results
            ]
            return ('' if not rows else
                    header((title, len(results)) +
                           ReportFormatter.header_for(results[0])[1:]) +
                    ''.join(rows))

        return self.HTML.format(
            ''.join([
                # FIXME print self.old_branch, self.new_branch
                table('Regression', self.comparator.decreased, 'red'),
                table('Improvement', self.comparator.increased, 'green'),
                ('' if self.changes_only else
                 table('No Changes', self.comparator.unchanged, 'black')),
                table('Added', self.comparator.added, ''),
                table('Removed', self.comparator.removed, '')
            ]))


def parse_args(args):
    """Parse command line arguments and set default values."""
    parser = argparse.ArgumentParser(description='Compare Performance tests.')
    parser.add_argument('--old-file',
                        help='Baseline performance test suite (csv file)',
                        required=True)
    parser.add_argument('--new-file',
                        help='New performance test suite (csv file)',
                        required=True)
    parser.add_argument('--format',
                        choices=['markdown', 'git', 'html'],
                        help='Output format. Default is markdown.',
                        default="markdown")
    parser.add_argument('--output', help='Output file name')
    parser.add_argument('--changes-only',
                        help='Output only affected tests', action='store_true')
    parser.add_argument('--new-branch',
                        help='Name of the new branch', default='NEW_MIN')
    parser.add_argument('--old-branch',
                        help='Name of the old branch', default='OLD_MIN')
    parser.add_argument('--delta-threshold',
                        help='Delta threshold. Default 0.05.',
                        type=float, default=0.05)
    return parser.parse_args(args)


def main():
    args = parse_args(sys.argv[1:])
    comparator = TestComparator(LogParser.results_from_file(args.old_file),
                                LogParser.results_from_file(args.new_file),
                                args.delta_threshold)
    formatter = ReportFormatter(comparator, args.old_branch, args.new_branch,
                                args.changes_only)
    formats = {
        'markdown': formatter.markdown,
        'git': formatter.git,
        'html': formatter.html
    }

    report = formats[args.format]()
    print(report)

    if args.output:
        with open(args.output, 'w') as f:
            f.write(report)


if __name__ == '__main__':
    sys.exit(main())
