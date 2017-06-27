#!/usr/bin/python
# -*- coding: utf-8 -*-

# ===--- test_compare_perf_tests.py --------------------------------------===//
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

import os
import shutil
import sys
import tempfile
import unittest

from StringIO import StringIO
from contextlib import contextmanager

from compare_perf_tests import LogParser
from compare_perf_tests import PerformanceTestResult
from compare_perf_tests import PerformanceTestSamples
from compare_perf_tests import ReportFormatter
from compare_perf_tests import ResultComparison
from compare_perf_tests import Sample
from compare_perf_tests import TestComparator
from compare_perf_tests import main
from compare_perf_tests import parse_args

# Multiple tests here would benefit from mocking, but as we need to run on
# Python 2.7, and the unittest.mock was added in Python 3.3. We're content with
# just observing side effects of propper interaction between our classes here.
# It is more pragmatic then hand-rolling all the mocks... Sorry!


@contextmanager
def captured_output():
    new_out, new_err = StringIO(), StringIO()
    old_out, old_err = sys.stdout, sys.stderr
    try:
        sys.stdout, sys.stderr = new_out, new_err
        yield sys.stdout, sys.stderr
    finally:
        sys.stdout, sys.stderr = old_out, old_err


class TestSample(unittest.TestCase):
    def test_has_named_fields(self):
        s = Sample(1,2,3)
        self.assertEquals(s.i, 1)
        self.assertEquals(s.num_iters, 2)
        self.assertEquals(s.runtime, 3)

    def test_is_iterable(self):
        s = Sample(1,2,3)
        self.assertEquals(s[0], 1)
        self.assertEquals(s[1], 2)
        self.assertEquals(s[2], 3)


class TestPerformanceTestSamples(unittest.TestCase):
    def setUp(self):
        self.rs = [Sample(*map(int, line.split())) for line in
                   '0 316 233,'  # this is anomalous sample - max
                   '1 4417 208, 2 4745 216, 3 4867 208, 4 4934 197,'
                   '5 5209 205, 6 4271 204, 7 4971 208, 8 5276 206,'
                   '9 4596 221, 10 5278 198'.split(',')]
        self.samples = PerformanceTestSamples('DropFirstAnyCollection')
        self.samples.add(self.rs[1])

    def test_has_name(self):
        self.assertEquals(self.samples.name, 'DropFirstAnyCollection')

    def test_stores_samples(self):
        self.assertEquals(self.samples.count, 1)
        s = self.samples.samples[0]
        self.assertTrue(isinstance(s, Sample))
        self.assertEquals(s.i, 1)
        self.assertEquals(s.num_iters, 4417)
        self.assertEquals(s.runtime, 208)

    def test_computes_min_max_median(self):
        self.assertEquals(self.samples.min, 208)
        self.assertEquals(self.samples.max, 208)
        self.assertEquals(self.samples.median, 208)
        self.samples.add(self.rs[2])
        self.assertEquals(self.samples.min, 208)
        self.assertEquals(self.samples.max, 216)
        self.assertEquals(self.samples.median, 216)
        self.samples.add(self.rs[4])
        self.assertEquals(self.samples.min, 197)
        self.assertEquals(self.samples.max, 216)
        self.assertEquals(self.samples.median, 208)

    def assertEqualStats(self, expected_stats):
        stats = (self.samples.mean, self.samples.sd, self.samples.cv)
        for actual, expected in zip(stats, expected_stats):
            self.assertAlmostEquals(actual, expected, places=2)

    def test_computes_mean_sd_cv(self):
        self.assertEqualStats((208.0, 0.0, 0.0))
        self.samples.add(self.rs[2])
        self.assertEqualStats((212.0, 5.66, 2.67 / 100))
        self.samples.add(self.rs[3])
        self.assertEqualStats((210.67, 4.62, 2.19 / 100))

    def test_init_with_samples(self):
        ss = PerformanceTestSamples('Lots', self.rs[1:])
        self.assertEquals(ss.count, 10)
        self.samples = ss
        self.assertEqualStats((207.10, 7.26, 3.51 / 100))

    def test_computes_range_spread(self):
        self.assertAlmostEquals(self.samples.range, 0)
        self.assertAlmostEquals(self.samples.spread, 0.0)
        self.samples.add(self.rs[2])
        self.assertAlmostEquals(self.samples.range, 8)
        self.assertAlmostEquals(self.samples.spread, 3.77 / 100, places=2)
        self.samples.add(self.rs[3])
        self.samples.add(self.rs[4])
        self.assertAlmostEquals(self.samples.range, 19)
        self.assertAlmostEquals(self.samples.spread, 9.17 / 100, places=2)

    def test_rejects_anomalous_samples(self):
        self.assertEquals(self.samples.count, 1)
        self.samples.add(self.rs[0])
        self.assertEquals(self.samples.count, 1)
        self.assertEquals(self.samples.anomalies[0], self.rs[0])

    def test_purges_anomalies(self):
        self.samples = PerformanceTestSamples('Anomaly', self.rs[:1])
        self.assertEquals(self.samples.count, 1)
        self.samples.add(self.rs[1])
        self.assertEquals(self.samples.count, 1)
        self.assertEquals(self.samples.anomalies[0], self.rs[0])


class TestPerformanceTestResult(unittest.TestCase):
    def test_init(self):
        log_line = '1,AngryPhonebook,20,10664,12933,11035,576,10884'
        r = PerformanceTestResult(log_line.split(','))
        self.assertEquals(r.name, 'AngryPhonebook')
        self.assertEquals(
            (r.num_samples, r.min, r.max, r.mean, r.sd, r.median),
            (20, 10664, 12933, 11035, 576, 10884))

        log_line = '1,AngryPhonebook,1,12045,12045,12045,0,12045,10510336'
        r = PerformanceTestResult(log_line.split(','))
        self.assertEquals(r.max_rss, 10510336)

    def test_repr(self):
        log_line = '1,AngryPhonebook,20,10664,12933,11035,576,10884'
        r = PerformanceTestResult(log_line.split(','))
        self.assertEquals(
            str(r),
            '<PerformanceTestResult name:\'AngryPhonebook\' samples:20 '
            'min:10664 max:12933 mean:11035 sd:576.0 median:10884>'
        )

    def test_merge(self):
        tests = """1,AngryPhonebook,1,12045,12045,12045,0,12045,10510336
1,AngryPhonebook,1,12325,12325,12325,0,12325,10510336
1,AngryPhonebook,1,11616,11616,11616,0,11616,10502144
1,AngryPhonebook,1,12270,12270,12270,0,12270,10498048""".split('\n')
        results = map(PerformanceTestResult,
                      [line.split(',') for line in tests])
        r = results[0]
        self.assertEquals((r.min, r.max), (12045, 12045))
        r.merge(results[1])
        self.assertEquals((r.min, r.max), (12045, 12325))
        r.merge(results[2])
        self.assertEquals((r.min, r.max), (11616, 12325))
        r.merge(results[3])
        self.assertEquals((r.min, r.max), (11616, 12325))


class TestResultComparison(unittest.TestCase):
    def setUp(self):
        self.r0 = PerformanceTestResult(
            '101,GlobalClass,20,0,0,0,0,0,10185728'.split(','))
        self.r01 = PerformanceTestResult(
            '101,GlobalClass,20,20,20,20,0,0,10185728'.split(','))
        self.r1 = PerformanceTestResult(
            '1,AngryPhonebook,1,12325,12325,12325,0,12325,10510336'.split(','))
        self.r2 = PerformanceTestResult(
            '1,AngryPhonebook,1,11616,11616,11616,0,11616,10502144'.split(','))

    def test_init(self):
        rc = ResultComparison(self.r1, self.r2)
        self.assertEquals(rc.name, 'AngryPhonebook')
        self.assertAlmostEquals(rc.ratio, 12325.0 / 11616.0)
        self.assertAlmostEquals(rc.delta, (((11616.0 / 12325.0) - 1) * 100),
                                places=3)
        # handle test results that sometimes change to zero, when compiler
        # optimizes out the body of the incorrectly written test
        rc = ResultComparison(self.r0, self.r0)
        self.assertEquals(rc.name, 'GlobalClass')
        self.assertAlmostEquals(rc.ratio, 1)
        self.assertAlmostEquals(rc.delta, 0, places=3)
        rc = ResultComparison(self.r0, self.r01)
        self.assertAlmostEquals(rc.ratio, 0, places=3)
        self.assertAlmostEquals(rc.delta, 2000000, places=3)
        rc = ResultComparison(self.r01, self.r0)
        self.assertAlmostEquals(rc.ratio, 20001)
        self.assertAlmostEquals(rc.delta, -99.995, places=3)
        # disallow comparison of different test results
        self.assertRaises(
            AssertionError,
            ResultComparison, self.r0, self.r1
        )

    def test_values_is_dubious(self):
        self.assertFalse(ResultComparison(self.r1, self.r2).is_dubious)
        self.r2.max = self.r1.min + 1
        # new.min < old.min < new.max
        self.assertTrue(ResultComparison(self.r1, self.r2).is_dubious)
        # other way around: old.min < new.min < old.max
        self.assertTrue(ResultComparison(self.r2, self.r1).is_dubious)


class FileSystemIntegration(unittest.TestCase):
    def setUp(self):
        # Create a temporary directory
        self.test_dir = tempfile.mkdtemp()

    def tearDown(self):
        # Remove the directory after the test
        shutil.rmtree(self.test_dir)

    def write_temp_file(self, file_name, data):
        temp_file_name = os.path.join(self.test_dir, file_name)
        with open(temp_file_name, 'w') as f:
            f.write(data)
        return temp_file_name


class OldAndNewLog(unittest.TestCase):
    old_log_content = """1,AngryPhonebook,20,10458,12714,11000,0,11000,10204365
2,AnyHashableWithAClass,20,247027,319065,259056,0,259056,10250445
3,Array2D,20,335831,400221,346622,0,346622,28297216
4,ArrayAppend,20,23641,29000,24990,0,24990,11149926
34,BitCount,20,3,4,4,0,4,10192896
35,ByteSwap,20,4,6,4,0,4,10185933"""

    new_log_content = """265,TwoSum,20,5006,5679,5111,0,5111
35,ByteSwap,20,0,0,0,0,0
34,BitCount,20,9,9,9,0,9
4,ArrayAppend,20,20000,29000,24990,0,24990
3,Array2D,20,335831,400221,346622,0,346622
1,AngryPhonebook,20,10458,12714,11000,0,11000"""

    old_results = dict([(r.name, r)
                        for r in
                        map(PerformanceTestResult,
                            [line.split(',')
                             for line in
                             old_log_content.splitlines()])])

    new_results = dict([(r.name, r)
                        for r in
                        map(PerformanceTestResult,
                            [line.split(',')
                             for line in
                             new_log_content.splitlines()])])

    def assert_report_contains(self, texts, report):
        assert not isinstance(texts, str)
        for text in texts:
            self.assertIn(text, report)


class TestLogParser(unittest.TestCase):
    def test_parse_results_verbose(self):
        """Parse multiple performance test results with 2 sample formats:
        single line for N = 1; two lines for N > 1.
        """
        verbose_log = """--- DATA ---
#,TEST,SAMPLES,MIN(us),MAX(us),MEAN(us),SD(us),MEDIAN(us)
Running AngryPhonebook for 3 samples.
    Measuring with scale 78.
    Sample 0,11812
    Measuring with scale 90.
    Sample 1,13898
    Measuring with scale 91.
    Sample 2,11467
1,AngryPhonebook,3,11467,13898,12392,1315,11812
Running Array2D for 3 samples.
    Sample 0,369900
    Sample 1,381039
    Sample 2,371043
3,Array2D,3,369900,381039,373994,6127,371043

Totals,2,381367,394937,386386,0,0"""
        parser = LogParser()
        results = parser.parse_results(verbose_log.split('\n'))

        r = results[0]
        self.assertEquals(
            (r.name, r.min, r.max, int(r.mean), int(r.sd), r.median),
            ('AngryPhonebook', 11467, 13898, 12392, 1315, 11812)
        )
        self.assertEquals(r.num_samples, r.samples.num_samples)
        self.assertEquals(results[0].samples.all_samples,
                          [(0, 78, 11812), (1, 90, 13898), (2, 91, 11467)])

        r = results[1]
        self.assertEquals(
            (r.name, r.min, r.max, int(r.mean), int(r.sd), r.median),
            ('Array2D', 369900, 381039, 373994, 6127, 371043)
        )
        self.assertEquals(r.num_samples, r.samples.num_samples)
        self.assertEquals(results[1].samples.all_samples,
                          [(0, 1, 369900), (1, 1, 381039), (2, 1, 371043)])

    def test_parse_results_csv(self):
        """Ignores header row, empty lines and Totals row"""
        log = """#,TEST,SAMPLES,MIN(us),MAX(us),MEAN(us),SD(us),MEDIAN(us)
34,BitCount,20,3,4,4,0,4

Totals,269,67351871,70727022,68220188,0,0,0"""
        parser = LogParser()
        results = parser.parse_results(log.splitlines())
        self.assertTrue(isinstance(results[0], PerformanceTestResult))
        self.assertEquals(results[0].name, 'BitCount')

    def test_parse_results_tab_delimited(self):
        """Ignores header row, empty lines and Totals row"""
        log = '34\tBitCount\t20\t3\t4\t4\t0\t4'
        parser = LogParser()
        results = parser.parse_results(log.splitlines())
        self.assertTrue(isinstance(results[0], PerformanceTestResult))
        self.assertEquals(results[0].name, 'BitCount')

    def test_parse_results_formatted_text(self):
        """Parse format that Benchmark_Driver prints to console"""
        log = ("""
# TEST      SAMPLES MIN(μs) MAX(μs) MEAN(μs) SD(μs) MEDIAN(μs) MAX_RSS(B)
3 Array2D        20    2060    2188     2099      0       2099   20915200
  Totals        281 2693794 2882846  2748843      0          0          0""")
        parser = LogParser()
        results = parser.parse_results(log.splitlines())
        self.assertTrue(isinstance(results[0], PerformanceTestResult))
        r = results[0]
        self.assertEquals(r.name, 'Array2D')
        self.assertEquals(r.max_rss, 20915200)

    def test_results_from_merge(self):
        """Parsing concatenated log merges same PerformanceTestResults"""
        concatenated_logs = """4,ArrayAppend,20,23641,29000,24990,0,24990
4,ArrayAppend,1,20000,20000,20000,0,20000"""
        results = LogParser.results_from_string(concatenated_logs)
        self.assertEquals(results.keys(), ['ArrayAppend'])
        result = results['ArrayAppend']
        self.assertTrue(isinstance(result, PerformanceTestResult))
        self.assertEquals(result.min, 20000)
        self.assertEquals(result.max, 29000)


class TestTestComparator(OldAndNewLog):
    def test_init(self):
        def names(tests):
            return [t.name for t in tests]

        tc = TestComparator(self.old_results, self.new_results, 0.05)
        self.assertEquals(names(tc.unchanged), ['AngryPhonebook', 'Array2D'])
        self.assertEquals(names(tc.increased), ['ByteSwap', 'ArrayAppend'])
        self.assertEquals(names(tc.decreased), ['BitCount'])
        self.assertEquals(names(tc.added), ['TwoSum'])
        self.assertEquals(names(tc.removed), ['AnyHashableWithAClass'])
        # other way around
        tc = TestComparator(self.new_results, self.old_results, 0.05)
        self.assertEquals(names(tc.unchanged), ['AngryPhonebook', 'Array2D'])
        self.assertEquals(names(tc.increased), ['BitCount'])
        self.assertEquals(names(tc.decreased), ['ByteSwap', 'ArrayAppend'])
        self.assertEquals(names(tc.added), ['AnyHashableWithAClass'])
        self.assertEquals(names(tc.removed), ['TwoSum'])
        # delta_threshold determines the sorting into change groups;
        # report only change above 100% (ByteSwap's runtime went to 0):
        tc = TestComparator(self.old_results, self.new_results, 1)
        self.assertEquals(
            names(tc.unchanged),
            ['AngryPhonebook', 'Array2D', 'ArrayAppend', 'BitCount']
        )
        self.assertEquals(names(tc.increased), ['ByteSwap'])
        self.assertEquals(tc.decreased, [])


class TestReportFormatter(OldAndNewLog):
    def setUp(self):
        super(TestReportFormatter, self).setUp()
        self.tc = TestComparator(self.old_results, self.new_results, 0.05)
        self.rf = ReportFormatter(self.tc, '', '', changes_only=False)
        self.markdown = self.rf.markdown()
        self.git = self.rf.git()
        self.html = self.rf.html()

    def assert_markdown_contains(self, texts):
        self.assert_report_contains(texts, self.markdown)

    def assert_git_contains(self, texts):
        self.assert_report_contains(texts, self.git)

    def assert_html_contains(self, texts):
        self.assert_report_contains(texts, self.html)

    def test_values(self):
        self.assertEquals(
            ReportFormatter.values(PerformanceTestResult(
                '1,AngryPhonebook,20,10664,12933,11035,576,10884'.split(','))),
            ('AngryPhonebook', '10664', '12933', '11035', '—')
        )
        self.assertEquals(
            ReportFormatter.values(PerformanceTestResult(
                '1,AngryPhonebook,1,12045,12045,12045,0,12045,10510336'
                .split(','))),
            ('AngryPhonebook', '12045', '12045', '12045', '10510336')
        )

        r1 = PerformanceTestResult(
            '1,AngryPhonebook,1,12325,12325,12325,0,12325,10510336'.split(','))
        r2 = PerformanceTestResult(
            '1,AngryPhonebook,1,11616,11616,11616,0,11616,10502144'.split(','))
        self.assertEquals(
            ReportFormatter.values(ResultComparison(r1, r2)),
            ('AngryPhonebook', '12325', '11616', '-5.8%', '1.06x')
        )
        self.assertEquals(
            ReportFormatter.values(ResultComparison(r2, r1)),
            ('AngryPhonebook', '11616', '12325', '+6.1%', '0.94x')
        )
        r2.max = r1.min + 1
        self.assertEquals(
            ReportFormatter.values(ResultComparison(r1, r2))[4],
            '1.06x (?)'  # is_dubious
        )

    def test_justified_columns(self):
        """Table columns are all formated with same width, defined by the
        longest value.
        """
        self.assert_markdown_contains([
            'AnyHashableWithAClass | 247027 | 319065 | 259056  | 10250445',
            'Array2D               | 335831 | 335831 | +0.0%   | 1.00x'])
        self.assert_git_contains([
            'AnyHashableWithAClass   247027   319065   259056    10250445',
            'Array2D                 335831   335831   +0.0%     1.00x'])

    def test_column_headers(self):
        """Report contains table headers for ResultComparisons and changed
        PerformanceTestResults.
        """
        performance_test_result = self.tc.added[0]
        self.assertEquals(
            ReportFormatter.header_for(performance_test_result),
            ('TEST', 'MIN', 'MAX', 'MEAN', 'MAX_RSS')
        )
        comparison_result = self.tc.increased[0]
        self.assertEquals(
            ReportFormatter.header_for(comparison_result),
            ('TEST', 'OLD', 'NEW', 'DELTA', 'SPEEDUP')
        )
        self.assert_markdown_contains([
            'TEST                  | OLD    | NEW    | DELTA   | SPEEDUP',
            '---                   | ---    | ---    | ---     | ---    ',
            'TEST                  | MIN    | MAX    | MEAN    | MAX_RSS'])
        self.assert_git_contains([
            'TEST                    OLD      NEW      DELTA     SPEEDUP',
            'TEST                    MIN      MAX      MEAN      MAX_RSS'])
        self.assert_html_contains([
            """
                <th align='left'>OLD</th>
                <th align='left'>NEW</th>
                <th align='left'>DELTA</th>
                <th align='left'>SPEEDUP</th>""",
            """
                <th align='left'>MIN</th>
                <th align='left'>MAX</th>
                <th align='left'>MEAN</th>
                <th align='left'>MAX_RSS</th>"""])

    def test_emphasize_speedup(self):
        """Emphasize speedup values for regressions and improvements"""
        # tests in No Changes don't have emphasized speedup
        self.assert_markdown_contains([
            'BitCount              | 3      | 9      | +199.9% | **0.33x**',
            'ByteSwap              | 4      | 0      | -100.0% | **4001.00x**',
            'AngryPhonebook        | 10458  | 10458  | +0.0%   | 1.00x ',
            'ArrayAppend           | 23641  | 20000  | -15.4%  | **1.18x (?)**'
        ])
        self.assert_git_contains([
            'BitCount                3        9        +199.9%   **0.33x**',
            'ByteSwap                4        0        -100.0%   **4001.00x**',
            'AngryPhonebook          10458    10458    +0.0%     1.00x',
            'ArrayAppend             23641    20000    -15.4%    **1.18x (?)**'
        ])
        self.assert_html_contains([
            """
        <tr>
                <td align='left'>BitCount</td>
                <td align='left'>3</td>
                <td align='left'>9</td>
                <td align='left'>+199.9%</td>
                <td align='left'><font color='red'>0.33x</font></td>
        </tr>""",
            """
        <tr>
                <td align='left'>ByteSwap</td>
                <td align='left'>4</td>
                <td align='left'>0</td>
                <td align='left'>-100.0%</td>
                <td align='left'><font color='green'>4001.00x</font></td>
        </tr>""",
            """
        <tr>
                <td align='left'>AngryPhonebook</td>
                <td align='left'>10458</td>
                <td align='left'>10458</td>
                <td align='left'>+0.0%</td>
                <td align='left'><font color='black'>1.00x</font></td>
        </tr>"""
        ])

    def test_sections(self):
        """Report is divided into sections with summaries."""
        self.assert_markdown_contains([
            """<details open>
  <summary>Regression (1)</summary>""",
            """<details >
  <summary>Improvement (2)</summary>""",
            """<details >
  <summary>No Changes (2)</summary>""",
            """<details open>
  <summary>Added (1)</summary>""",
            """<details open>
  <summary>Removed (1)</summary>"""])
        self.assert_git_contains([
            'Regression (1): \n',
            'Improvement (2): \n',
            'No Changes (2): \n',
            'Added (1): \n',
            'Removed (1): \n'])
        self.assert_html_contains([
            "<th align='left'>Regression (1)</th>",
            "<th align='left'>Improvement (2)</th>",
            "<th align='left'>No Changes (2)</th>",
            "<th align='left'>Added (1)</th>",
            "<th align='left'>Removed (1)</th>"])

    def test_report_only_changes(self):
        """Leave out tests without significant change."""
        rf = ReportFormatter(self.tc, '', '', changes_only=True)
        markdown, git, html = rf.markdown(), rf.git(), rf.html()
        self.assertNotIn('No Changes', markdown)
        self.assertNotIn('AngryPhonebook', markdown)
        self.assertNotIn('No Changes', git)
        self.assertNotIn('AngryPhonebook', git)
        self.assertNotIn('No Changes', html)
        self.assertNotIn('AngryPhonebook', html)


class Test_parse_args(unittest.TestCase):
    required = ['--old-file', 'old.log', '--new-file', 'new.log']

    def test_required_input_arguments(self):
        with captured_output() as (_, err):
            self.assertRaises(SystemExit, parse_args, [])
        self.assertIn('usage: compare_perf_tests.py', err.getvalue())

        args = parse_args(self.required)
        self.assertEquals(args.old_file, 'old.log')
        self.assertEquals(args.new_file, 'new.log')

    def test_format_argument(self):
        self.assertEquals(parse_args(self.required).format, 'markdown')
        self.assertEquals(
            parse_args(self.required + ['--format', 'markdown']).format,
            'markdown')
        self.assertEquals(
            parse_args(self.required + ['--format', 'git']).format, 'git')
        self.assertEquals(
            parse_args(self.required + ['--format', 'html']).format, 'html')

        with captured_output() as (_, err):
            self.assertRaises(SystemExit, parse_args,
                              self.required + ['--format', 'bogus'])
        self.assertIn("error: argument --format: invalid choice: 'bogus' "
                      "(choose from 'markdown', 'git', 'html')",
                      err.getvalue())

    def test_delta_threshold_argument(self):
        # default value
        args = parse_args(self.required)
        self.assertEquals(args.delta_threshold, 0.05)
        # float parsing
        args = parse_args(self.required + ['--delta-threshold', '0.1'])
        self.assertEquals(args.delta_threshold, 0.1)
        args = parse_args(self.required + ['--delta-threshold', '1'])
        self.assertEquals(args.delta_threshold, 1.0)
        args = parse_args(self.required + ['--delta-threshold', '.2'])
        self.assertEquals(args.delta_threshold, 0.2)

        with captured_output() as (_, err):
            self.assertRaises(SystemExit, parse_args,
                              self.required + ['--delta-threshold', '2,2'])
        self.assertIn(" error: argument --delta-threshold: invalid float "
                      "value: '2,2'",
                      err.getvalue())

    def test_output_argument(self):
        self.assertEquals(parse_args(self.required).output, None)
        self.assertEquals(parse_args(self.required +
                                     ['--output', 'report.log']).output,
                          'report.log')

    def test_changes_only_argument(self):
        self.assertFalse(parse_args(self.required).changes_only)
        self.assertTrue(parse_args(self.required +
                                   ['--changes-only']).changes_only)

    def test_branch_arguments(self):
        # default value
        args = parse_args(self.required)
        self.assertEquals(args.new_branch, 'NEW_MIN')
        self.assertEquals(args.old_branch, 'OLD_MIN')
        # user specified
        args = parse_args(
            self.required + ['--old-branch', 'master',
                             '--new-branch', 'amazing-optimization'])
        self.assertEquals(args.old_branch, 'master')
        self.assertEquals(args.new_branch, 'amazing-optimization')


class Test_compare_perf_tests_main(OldAndNewLog, FileSystemIntegration):
    """Integration test that invokes the whole comparison script."""
    markdown = [
        '<summary>Regression (1)</summary>',
        'TEST                  | OLD    | NEW    | DELTA   | SPEEDUP',
        'BitCount              | 3      | 9      | +199.9% | **0.33x**',
    ]
    git = [
        'Regression (1):',
        'TEST                    OLD      NEW      DELTA     SPEEDUP',
        'BitCount                3        9        +199.9%   **0.33x**',
    ]
    html = ['<html>', "<td align='left'>BitCount</td>"]

    def setUp(self):
        super(Test_compare_perf_tests_main, self).setUp()
        self.old_log = self.write_temp_file('old.log', self.old_log_content)
        self.new_log = self.write_temp_file('new.log', self.new_log_content)

    def execute_main_with_format(self, report_format, test_output=False):
        report_file = self.test_dir + 'report.log'
        args = ['compare_perf_tests.py',
                '--old-file', self.old_log,
                '--new-file', self.new_log,
                '--format', report_format]

        sys.argv = (args if not test_output else
                    args + ['--output', report_file])

        with captured_output() as (out, _):
            main()
        report_out = out.getvalue()

        if test_output:
            with open(report_file, 'r') as f:
                report = f.read()
            # because print adds newline, add one here, too:
            report_file = str(report + '\n')
        else:
            report_file = None

        return report_out, report_file

    def test_markdown(self):
        """Writes Markdown formatted report to stdout"""
        report_out, _ = self.execute_main_with_format('markdown')
        self.assert_report_contains(self.markdown, report_out)

    def test_markdown_output(self):
        """Writes Markdown formatted report to stdout and `--output` file."""
        report_out, report_file = (
            self.execute_main_with_format('markdown', test_output=True))
        self.assertEquals(report_out, report_file)
        self.assert_report_contains(self.markdown, report_file)

    def test_git(self):
        """Writes Git formatted report to stdout."""
        report_out, _ = self.execute_main_with_format('git')
        self.assert_report_contains(self.git, report_out)

    def test_git_output(self):
        """Writes Git formatted report to stdout and `--output` file."""
        report_out, report_file = (
            self.execute_main_with_format('git', test_output=True))
        self.assertEquals(report_out, report_file)
        self.assert_report_contains(self.git, report_file)

    def test_html(self):
        """Writes HTML formatted report to stdout."""
        report_out, _ = self.execute_main_with_format('html')
        self.assert_report_contains(self.html, report_out)

    def test_html_output(self):
        """Writes HTML formatted report to stdout and `--output` file."""
        report_out, report_file = (
            self.execute_main_with_format('html', test_output=True))
        self.assertEquals(report_out, report_file)
        self.assert_report_contains(self.html, report_file)


if __name__ == '__main__':
    unittest.main()
