#!/bin/python3
# https://github.com/SimonLammer/testpythonscript
"""
This can be used to test other python scripts.
Other scripts will be imported and tested in a new subprocess each.
"""

import argparse
from contextlib import contextmanager
from datetime import datetime, timedelta
from functools import wraps
from importlib import import_module, reload
import io
import multiprocessing
import os
from pathlib import Path
import sys
import threading
import time
import traceback
from typing import Callable, Dict, List, Optional, Tuple
import unittest

TESTSUITE_DESCRIPTION = "testpythonscript sample testsuite" # displayed in help message

LIBRARY = None # This will be set to the imported script
LIBRARY_LOAD_STDIN = "patched stdin whilst loading the library" # This will be fed to stdin while the library is loading

def reload_library(stdin=""):
  '''Decorator for reloading the library stored in LIBRARY'''
  def outer(func):
    @wraps(func)
    def inner(*args, **kwargs):
      global LIBRARY
      with patched_io(stdin) as (_, stdout, stderr):
        LIBRARY = reload(LIBRARY)
      func(*args, stdout, stderr, **kwargs)
    return inner
  return outer

class Test(unittest.TestCase):
  def test_initial_attributes(self):
    self.assertTrue(hasattr(LIBRARY, "LIVES"))
    self.assertEqual(LIBRARY.LIVES, 3)

  def test_foo(self):
    self.assertTrue(hasattr(LIBRARY, "foo"))
    with patched_io() as (stdin, stdout, stderr):
      LIBRARY.foo()
      self.assertEqual("foo\nbar\n", stdout.getvalue())

  def test_false(self):
    self.skipTest("ignore")
    self.assertTrue(LIBRARY.return_true())

  @reload_library(stdin="Some stdin patch")
  def test_zoo(self, load_stdout, load_stderr):
    self.assertEqual(f"Library load input() returned: Some stdin patch\n", load_stdout.readlines()[1])




def test(filename: Path):
  global LIBRARY
  with load_library(filename, LIBRARY_LOAD_STDIN) as (lib, lib_load_stdout, lib_load_stderr):
    LIBRARY = lib
    print("Library load stderr:")
    print(lib_load_stderr.getvalue())
    unittest.main(argv=['first-arg-is-ignored'], verbosity=2, exit=False) # https://medium.com/@vladbezden/using-python-unittest-in-ipython-or-jupyter-732448724e31
    LIBRARY = None

  print(f"Completed testing {filename}")

################################################################################
# The testsuite's internals are below.
# You shouldn't need to edit the rest of the file. (Please submit a pull request
#   to https://github.com/SimonLammer/testpythonscript otherwise)
################################################################################

LOAD_TIMEOUT = None       # cli arg; Terminate the subprocess if importing the library takes longer
COMPLETION_TIMEOUT = None # cli arg; Terminate the subprocess if takes longer to finish gracefully

WAIT_DELAY = timedelta(milliseconds=50)
OUTPUT_REPORT_DELAY = timedelta(milliseconds=5)
TERMINATION_DELAY = OUTPUT_REPORT_DELAY + timedelta(milliseconds=2)

COMMUNICATION_QUEUE = multiprocessing.Queue() # for communication with subprocesses

@contextmanager
def patched_io(initial_in=None) -> Tuple[io.StringIO, io.StringIO, io.StringIO]:
  new_in, new_out, new_err = io.StringIO(initial_in), io.StringIO(), io.StringIO()
  old_in, old_out, old_err = sys.stdin, sys.stdout, sys.stderr
  try:
    sys.stdin, sys.stdout, sys.stderr = new_in, new_out, new_err
    yield sys.stdin, sys.stdout, sys.stderr
  finally:
    sys.stdout.seek(0)
    sys.stderr.seek(0)
    sys.stdin, sys.stdout, sys.stderr = old_in, old_out, old_err

def main():
  args = parse_args()
  scripts: List[Path] = args.script
  max_processes = args.processes
  global LOAD_TIMEOUT
  LOAD_TIMEOUT = args.load_timeout
  global COMPLETION_TIMEOUT
  COMPLETION_TIMEOUT = args.completion_timeout

  # queue = multiprocessing.Queue() # for communication with subprocesses

  index_pid = {} # key: index, value: pid

  outputs: Dict[int, str] = {} # key: pid, value: stdout & stderr for each script
  exitcodes: Dict[int, int] = {} # key: pid, value: exitcode

  timeouts: List[Tuple[int, datetime]] = [] # [(pid, datetime), (...), ...]

  ready: List[multiprocessing.Process] = list(map(lambda x: multiprocessing.Process(target=runtest, args=(test, *x, COMMUNICATION_QUEUE)), enumerate(scripts)))
  running: List[multiprocessing.Process] = []

  while len(ready) > 0 or len(running) > 0:
    for i, p in enumerate(running):
      if not p.is_alive():
        print(f"process {p.pid} finished with exit code {p.exitcode}")
        exitcodes[p.pid] = p.exitcode
        p.join()
        del running[i]

    while 0 < len(ready) and len(running) < max_processes:
      process = ready.pop()
      running.append(process)
      process.start()

    # print("timeouts", timeouts)
    while COMMUNICATION_QUEUE.empty() and (len(timeouts) == 0 or timeouts[0][1] > datetime.now()) and (len(running) > 0 and running[0].is_alive()):
      time.sleep(WAIT_DELAY.total_seconds())

    while not COMMUNICATION_QUEUE.empty():
      pid, item = COMMUNICATION_QUEUE.get_nowait()
      if pid not in index_pid.values(): # item is the process index
        print(f"process {pid} is processing {scripts[item]}")
        index_pid[item] = pid
      elif isinstance(item, str): # process sent its output
        # print(f"process {pid} sent its output")
        outputs[pid] = item
      elif isinstance(item, datetime): # process set timeout
        # print(f"setting timeout of pid {pid} to {item}")
        for i, (p, t) in enumerate(timeouts):
          if p == pid:
            timeouts[i] = (pid, item)
            break
        else:
          timeouts.append((pid, item))
      elif item is None: # process canceled timeout
        for i, (p, t) in enumerate(timeouts):
          if pid == p:
            # print(f"canceling timout of pid {pid} ({t})")
            del timeouts[i]
      else:
        # pass
        raise RuntimeWarning("Invalid item in queue", item)

    timeouts.sort(key=lambda x: x[1])
    time.sleep(TERMINATION_DELAY.total_seconds()) # give timeouted processes enough time to report their output
    for pid, t in timeouts:
      if t > datetime.now():
        break
      for i, p in enumerate(running):
        if pid == p.pid and p.is_alive():
          print(f"process {pid} exceeded its timeout {t} by {datetime.now() - t}, terminating")
          p.terminate()

  for i in range(len(scripts)):
    pid = index_pid[i]
    print('+' * 80)
    print(f"Output of {scripts[i]} test (exitcode {exitcodes[pid]}):")
    output = outputs[pid]
    if output is not None:
      print(''.join(output))

def parse_args():
  def filetype(filepath):
    path = Path(filepath)
    if not path.is_file():
      raise argparse.ArgumentTypeError(f"{filepath} does not exist!")
    return path

  parser = argparse.ArgumentParser(description=TESTSUITE_DESCRIPTION, formatter_class=argparse.ArgumentDefaultsHelpFormatter)
  parser.add_argument('-p', '--processes',
    help="Maximum number of processes to use in parallel.",
    type=int,
    default=os.cpu_count())
  parser.add_argument('-l', '--load-timeout',
    help="A test will be terminated if loading the script takes longer than this many milliseconds.",
    type=lambda x: timedelta(milliseconds=float(x)),
    default="1000")
  parser.add_argument('-c', '--completion-timeout',
    help="A test will be terminated if it takes longer than this many milliseconds.",
    type=lambda x: timedelta(milliseconds=float(x)),
    default="60000")
  parser.add_argument('script',
    help="The script file to test. MUST end in '.py' (without quotes)!",
    nargs='+',
    type=filetype)
  return parser.parse_args()

def runtest(test_function: Callable, index: int, scriptpath: Path, queue: multiprocessing.Queue = COMMUNICATION_QUEUE, library_load_stdin : str = LIBRARY_LOAD_STDIN):
  output = io.StringIO()
  sys.stdout = output
  sys.stderr = output
  try:
    pid = multiprocessing.current_process().pid
    queue.put((pid, index))

    def reportoutput():
      while True:
        queue.put((pid, output.getvalue()))
        time.sleep(OUTPUT_REPORT_DELAY.total_seconds())
    t = threading.Thread(target=reportoutput, daemon=True)
    t.start()

    # original_stdin, original_stdout, original_stderr = sys.stdin, sys.stdout, sys.stderr
    # with patched_io(library_load_stdin) as (_, stdout, stderr):
      # def testwrapper(lib, _):
      #   sys.stdin, sys.stdout, sys.stderr = original_stdin, original_stdout, original_stderr
      #   queue.put((pid, datetime.now() + COMPLETION_TIMEOUT))
      #   test_function(lib, scriptpath, stdout, stderr)
      # queue.put((pid, datetime.now() + LOAD_TIMEOUT))
      # testscript(scriptpath, testwrapper)
    queue.put((pid, datetime.now() + COMPLETION_TIMEOUT))
    test(scriptpath)
  except:
    traceback.print_exception(*sys.exc_info())
  queue.put((pid, output.getvalue()))

@contextmanager
def load_library(path: Path, stdin: str = ''):
  # https://stackoverflow.com/a/52328080/2808520
  '''
  Runs some tests with the given script.
  '''
  assert(path.name.endswith('.py')) # thwart ModuleNotFoundError 
  imported_library = None
  try:
    sys.path.insert(0, str(path.parent.absolute()))
    with patched_io(stdin) as (_, stdout, stderr):
      imported_library = import_module(path.name[:-3])
    yield imported_library, stdout, stderr
  finally:
    del imported_library
    sys.path.pop(0)

if __name__ == '__main__':
  main()
