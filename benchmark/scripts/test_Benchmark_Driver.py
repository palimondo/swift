#!/usr/bin/python
# -*- coding: utf-8 -*-

# ===--- test_Benchmark_Driver.py ----------------------------------------===//
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
import unittest

from imp import load_source

from test_utils import captured_output
from compare_perf_tests import PerformanceTestResult
# import Benchmark_Driver  # doesn't work because it misses '.py' extension
Benchmark_Driver = load_source(
    'Benchmark_Driver', os.path.join(os.path.dirname(
        os.path.abspath(__file__)), 'Benchmark_Driver'))
# from Benchmark_Driver import parse_args
parse_args = Benchmark_Driver.parse_args
BenchmarkDriver = Benchmark_Driver.BenchmarkDriver


class Test_parse_args(unittest.TestCase):
    required_submit = 'submit -m machine -r 123456 -l LNT_host'.split()

    def assert_contains(self, texts, output):
        assert not isinstance(texts, str)
        for text in texts:
            self.assertIn(text, output)

    def test_requires_command_argument(self):
        with captured_output() as (_, err):
            self.assertRaises(SystemExit, parse_args, [])
        self.assert_contains(['usage:', 'Benchmark_Driver'], err.getvalue())

    def test_command_help_lists_commands(self):
        with captured_output() as (out, _):
            self.assertRaises(SystemExit, parse_args, ['-h'])
        self.assert_contains(['COMMAND', 'run', 'compare', 'submit'],
                             out.getvalue())

    def test_run_benchmarks_by_name_or_ordinal(self):
        benchmarks = ['AngryPhonebook', '42']
        self.assertEquals(
            parse_args(['run'] + benchmarks).benchmarks, benchmarks)
        self.assertEquals(
            parse_args(self.required_submit + benchmarks).benchmarks,
            benchmarks)

    def test_run_benchmarks_matching_pattern(self):
        regexes = ['Prefix', '.*Suffix.*']
        filters = ['-f', regexes[0], '-f', regexes[1]]
        self.assertEquals(parse_args(['run'] + filters).filters, regexes)
        self.assertEquals(
            parse_args(self.required_submit + filters).filters, regexes)

    def test_run_benchmarks_and_filters_are_exclusive(self):
        with captured_output() as (_, err):
            self.assertRaises(SystemExit,
                              parse_args, 'run -f Filter1 Benchmark1'.split())
        self.assert_contains(
            ['error',
             'argument BENCHMARK: not allowed with argument -f/--filter'],
            err.getvalue())

    def test_tests_location(self):
        here = os.path.dirname(os.path.abspath(__file__))
        self.assertEquals(parse_args(['run']).tests, here)
        tests = '/benchmarks/are/here'
        self.assertEquals(parse_args(['run', '-t', tests]).tests, tests)

    def test_optimization_argument(self):
        self.assertEquals(parse_args(['run']).optimization, 'O')
        self.assertEquals(
            parse_args(['run', '-o', 'O']).optimization, 'O')
        self.assertEquals(
            parse_args(['run', '-o', 'Onone']).optimization, 'Onone')
        self.assertEquals(
            parse_args(['run', '-o', 'Ounchecked']).optimization, 'Ounchecked')

        with captured_output() as (_, err):
            self.assertRaises(SystemExit,
                              parse_args, ['run', '-o', 'bogus'])
        self.assert_contains(
            ['error:',
             "argument -o/--optimization: invalid choice: 'bogus'",
             "(choose from 'O', 'Onone', 'Ounchecked')"],
            err.getvalue())


class ArgsStub(object):
    def __init__(self):
        self.benchmarks = None
        self.filters = None
        self.tests = '/benchmarks/'
        self.optimization = 'O'
        self.iterations = 1


class SubprocessMock(object):
    """Mock for subprocess module's check_output method."""
    STDOUT = object()
    def __init__(self, responses=None):
        self.calls = []
        self.expected = []
        self.respond = dict()
        responses = responses or []
        for call_args, response in responses:
            self.expect(call_args, response)

        def _check_output(args, stdin=None, stdout=None, stderr=None,
                          shell=False):
            return self.record_and_respond(args, stdin, stdout, stderr, shell)
        self.check_output = _check_output

    def expect(self, call_args, response):
        call_args = tuple(call_args)
        self.expected.append(call_args)
        self.respond[call_args] = response

    def record_and_respond(self, args, stdin, stdout, stderr, shell):
        _ = stdin, stdout, shell  # ignored in mock
        assert stderr == self.STDOUT, 'Errors are NOT redirected to STDOUT'
        args = tuple(args)
        self.calls.append(args)
        return self.respond.get(args, '')

    def assert_called_with(self, expected_args):
        expected_args = tuple(expected_args)
        assert expected_args in self.calls, (
            'Expected: {0} in Called: {1}'.format(expected_args, self.calls))

    def assert_called_all_expected(self):
        assert self.calls == self.expected, (
            '\nExpected: {0}, \n  Called: {1}'.format(self.expected, self.calls))


class TestBenchmarkDriverInitialization(unittest.TestCase):
    def setUp(self):
        self.args = ArgsStub()
        self.subprocess_mock = SubprocessMock()

    def test_test_harness(self):
        self.assertEquals(
            BenchmarkDriver(self.args, tests=['ignored']).test_harness,
            '/benchmarks/Benchmark_O')
        self.args.tests = '/path'
        self.args.optimization = 'Suffix'
        self.assertEquals(
            BenchmarkDriver(self.args, tests=['ignored']).test_harness,
            '/path/Benchmark_Suffix')

    def test_gets_list_of_precommit_benchmarks(self):
        self.subprocess_mock.expect(
            '/benchmarks/Benchmark_O --list'.split(),
            'Enabled Tests:\tBenchmark1\n\tBenchmark2\n')
        driver = BenchmarkDriver(
            self.args, _subprocess=self.subprocess_mock)
        self.subprocess_mock.assert_called_all_expected()
        self.assertEquals(driver.tests,
                          ['Benchmark1', 'Benchmark2'])
        self.assertEquals(driver.all_tests,
                          ['Benchmark1', 'Benchmark2'])

    list_all_tests = (
        '/benchmarks/Benchmark_O --list --run-all'.split(),
        'Enabled Tests:\tBenchmark1\n\tBenchmark2\n\tBenchmark3\n')

    def test_gets_list_of_all_benchmarks_when_benchmarks_args_exist(self):
        self.args.benchmarks = '1 Benchmark3 bogus'.split()
        self.subprocess_mock.expect(*self.list_all_tests)
        driver = BenchmarkDriver(
            self.args, _subprocess=self.subprocess_mock)
        self.subprocess_mock.assert_called_all_expected()
        self.assertEquals(driver.tests, ['1', 'Benchmark3'])
        self.assertEquals(driver.all_tests,
                          ['Benchmark1', 'Benchmark2', 'Benchmark3'])

    def test_filters_benchmarks_by_pattern(self):
        self.args.filters = '-f .+3'.split()
        self.subprocess_mock.expect(*self.list_all_tests)
        driver = BenchmarkDriver(
            self.args, _subprocess=self.subprocess_mock)
        self.subprocess_mock.assert_called_all_expected()
        self.assertEquals(driver.tests, ['Benchmark3'])
        self.assertEquals(driver.all_tests,
                          ['Benchmark1', 'Benchmark2', 'Benchmark3'])


class LogParserStub(object):
    results_from_string_called = False

    @staticmethod
    def results_from_string(log_contents):
        LogParserStub.results_from_string_called = True
        r = PerformanceTestResult('3,b1,1,123,123,123,0,123'.split(','))
        # r.samples =
        return {'b1': r}

class TestBenchmarkDriverRunningTests(unittest.TestCase):
    def setUp(self):
        self.args = ArgsStub()
        self.parser_stub = LogParserStub()
        self.subprocess_mock = SubprocessMock()
        self.subprocess_mock.expect(
            '/benchmarks/Benchmark_O --list'.split(),
            'Enabled Tests:\tb1\n')
        self.driver = BenchmarkDriver(
            self.args, _subprocess=self.subprocess_mock,
            parser=self.parser_stub)

    def test_sample_benchmark_multiple_times(self):
        self.driver.args.iterations = 3
        self.driver.run('b1')
        self.subprocess_mock.assert_called_with(
            ('/benchmarks/Benchmark_O', 'b1', '--num-samples=3'))
        self.driver.run('b2', num_samples=5)
        self.subprocess_mock.assert_called_with(
            ('/benchmarks/Benchmark_O', 'b2', '--num-samples=5'))

    def test_run_benchmark_with_specified_number_of_iterations(self):
        self.driver.run('b', num_iters=7)
        self.subprocess_mock.assert_called_with(
            ('/benchmarks/Benchmark_O', 'b', '--num-iters=7'))

    def test_run_benchmark_in_verbose_mode(self):
        self.driver.run('b', verbose=True)
        self.subprocess_mock.assert_called_with(
            ('/benchmarks/Benchmark_O', 'b', '--verbose'))

    def test_parse_results_from_running_benchmarks(self):
        self.driver.run('b')
        self.assertTrue(self.parser_stub.results_from_string_called)

    def test_measure_memory_used_by_test_on_darwin(self):
        self.driver.args.measure_memory = True
        self.subprocess_mock.expect(
            'time -lp /benchmarks/Benchmark_O b1'.split(),
            '  12345678  maximum resident set size' + ''.join(['\n']*14))
        r = self.driver.run('b1', measure_memory=True, platform='darwin')
        self.subprocess_mock.assert_called_all_expected()
        self.assertEquals(r.max_rss, 12345678)

    def test_measure_memory_used_by_test_on_linux(self):
        self.driver.args.measure_memory = True
        self.subprocess_mock.expect(
            'time --verbose /benchmarks/Benchmark_O b1'.split(),
            '    Maximum resident set size (kbytes): 12345' +
            ''.join(['\n']*14))
        r = self.driver.run('b1', measure_memory=True, platform='linux2')
        self.subprocess_mock.assert_called_all_expected()
        self.assertEquals(r.max_rss, 12641280)


if __name__ == '__main__':
    unittest.main()
