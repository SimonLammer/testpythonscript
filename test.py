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
import unittest # This module is not necessary for the testsuite, but will probably make testing the script easier


LIBRARY = None # This will be set to the imported script
LIBRARY_PATH = None # This will be set to the imported script's path

def load_library_patched(stdin=""):
  '''Decorator for reloading the library stored in LIBRARY'''
  def outer(func):
    @wraps(func)
    def inner(*args, **kwargs):
      global LIBRARY
      with patched_io(stdin) as (_, stdout, stderr):
        LIBRARY = load_library(LIBRARY_PATH)
      func(*args, stdout, stderr, **kwargs)
    return inner
  return outer


################################################################################
# Add your tests below this comment.
################################################################################


TESTSUITE_DESCRIPTION = "testpythonscript sample testsuite" # displayed in help message

def test_main(library_path: Path):
  global LIBRARY_PATH
  LIBRARY_PATH = library_path
  unittest.main(argv=['first-arg-is-ignored'], verbosity=2, exit=False) # https://medium.com/@vladbezden/using-python-unittest-in-ipython-or-jupyter-732448724e31

  print(f"Completed testing {library_path}")

class Test(unittest.TestCase):
  @load_library_patched(stdin="\n")
  def test_initial_attributes(self, load_stdout, load_stderr):
    self.assertTrue(hasattr(LIBRARY, "LIVES"))
    self.assertEqual(LIBRARY.LIVES, 3)

  @load_library_patched(stdin="\n")
  def test_foo(self, load_stdout, load_stderr):
    self.assertTrue(hasattr(LIBRARY, "foo"))
    with patched_io() as (stdin, stdout, stderr):
      LIBRARY.foo()
      self.assertEqual("foo\nbar\n", stdout.getvalue())

  @load_library_patched(stdin="\n")
  def test_false(self, load_stdout, load_stderr):
    self.assertTrue(LIBRARY.return_true())

  @load_library_patched(stdin="Some stdin patch")
  def test_library_load_stdin(self, load_stdout, load_stderr):
    self.assertEqual(f"Library load input() returned: Some stdin patch\n", load_stdout.readlines()[1])


################################################################################
# The testsuite's internals are below.
# You shouldn't need to edit the rest of the file. (Please submit a pull request
#   to https://github.com/SimonLammer/testpythonscript otherwise)
################################################################################


TIMEOUT = None # cli arg; Terminate the subprocess if takes longer to finish gracefully

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
  global TIMEOUT
  TIMEOUT = args.timeout

  # queue = multiprocessing.Queue() # for communication with subprocesses

  index_pid = {} # key: index, value: pid

  outputs: Dict[int, str] = {} # key: pid, value: stdout & stderr for each script
  exitcodes: Dict[int, int] = {} # key: pid, value: exitcode

  timeouts: List[Tuple[int, datetime]] = [] # [(pid, datetime), (...), ...]

  ready: List[multiprocessing.Process] = list(map(lambda x: multiprocessing.Process(target=runtest, args=(test_main, *x, COMMUNICATION_QUEUE)), enumerate(scripts)))
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
  parser.add_argument('-t', '--timeout',
    help="A test will be terminated if it takes longer than this many seconds.",
    type=lambda x: timedelta(seconds=float(x)),
    default="60")
  parser.add_argument('script',
    help="The script file to test. MUST end in '.py' (without quotes)!",
    nargs='+',
    type=filetype)
  return parser.parse_args()

def runtest(test_function: Callable, index: int, scriptpath: Path, queue: multiprocessing.Queue = COMMUNICATION_QUEUE):
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

    queue.put((pid, datetime.now() + TIMEOUT))
    test_function(scriptpath)
  except:
    traceback.print_exception(*sys.exc_info())
  queue.put((pid, output.getvalue()))

LOADED_LIBRARIES = {}
def load_library(path: Path):
  '''
  Runs some tests with the given script.
  '''
  assert(path.name.endswith('.py')) # thwart ModuleNotFoundError 
  lib = LOADED_LIBRARIES.get(path)
  if lib:
    lib = reload(lib)
  else:
    # https://stackoverflow.com/a/52328080/2808520
    sys.path.insert(0, str(path.parent.absolute()))
    lib = import_module(path.name[:-3])
  LOADED_LIBRARIES[path] = lib
  return lib

if __name__ == '__main__':
  main()
