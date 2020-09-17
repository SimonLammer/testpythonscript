#!/bin/python3
"""
This can be used to test other python scripts.
Other scripts will be imported and tested in a new subprocess each.
"""

import argparse
from contextlib import contextmanager
from datetime import datetime, timedelta
from importlib import import_module
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

class Test(unittest.TestCase):
  def test_initial_attributes(self):
    self.assertTrue(hasattr(LIBRARY, "LIVES"))
    self.assertEqual(LIBRARY.LIVES, 3)

  def test_foo(self):
    self.assertTrue(hasattr(LIBRARY, "foo"))
    with captured_output() as (out, err):
      LIBRARY.foo()
      self.assertEqual("foo\nbar\n", out.getvalue())

  def test_false(self):
    self.fail("Message")

@contextmanager
def captured_output() -> Tuple[io.StringIO, io.StringIO]:
  new_out, new_err = io.StringIO(), io.StringIO()
  old_out, old_err = sys.stdout, sys.stderr
  try:
    sys.stdout, sys.stderr = new_out, new_err
    yield sys.stdout, sys.stderr
  finally:
    sys.stdout, sys.stderr = old_out, old_err

def test(lib, filename: Path):
  global LIBRARY
  LIBRARY = lib
  unittest.main(argv=['first-arg-is-ignored'], verbosity=2, exit=False) # https://medium.com/@vladbezden/using-python-unittest-in-ipython-or-jupyter-732448724e31
  LIBRARY = None
  print(f"Completed testing {filename}")

################################################################################
# The testsuite's internals are below.
# You shouldn't need to edit the rest of the file. (Please submit a pull request
#   to https://github.com/SimonLammer/testpythonscript otherwise)
################################################################################

LIBRARY_LOAD_TIMEOUT = timedelta(milliseconds=500)
# TODO: completion timeout
OUTPUT_REPORT_DELAY = timedelta(milliseconds=5)
WAIT_DELAY = timedelta(milliseconds=50)
TERMINATION_DELAY = OUTPUT_REPORT_DELAY + timedelta(milliseconds=2)

def main():
  args = parse_args()
  scripts: List[Path] = args.script
  max_processes = args.processes

  queue = multiprocessing.Queue() # for communication with subprocesses

  index_pid = {} # key: index, value: pid

  outputs: Dict[int, str] = {} # key: pid, value: stdout & stderr for each script
  exitcodes: Dict[int, int] = {} # key: pid, value: exitcode

  timeouts: List[Tuple[int, datetime]] = [] # [(pid, datetime), (...), ...]

  ready: List[multiprocessing.Process] = list(map(lambda x: multiprocessing.Process(target=runtest, args=(*x, queue)), enumerate(scripts)))
  running: List[multiprocessing.Process] = []

  while len(ready) > 0 or len(running) > 0:
    while 0 < len(ready) and len(running) < max_processes:
      process = ready.pop()
      running.append(process)
      process.start()

    # print("timeouts", timeouts)
    while queue.empty() and (len(timeouts) == 0 or timeouts[0][1] > datetime.now()) and running[0].is_alive():
      time.sleep(WAIT_DELAY.total_seconds())
    
    while not queue.empty():
      pid, item = queue.get_nowait()
      if pid not in index_pid.values(): # item is the process index
        print(f"process {pid} is processing {scripts[item]}")
        index_pid[item] = pid
      elif isinstance(item, str): # process sent its output
        # print(f"process {pid} sent its output")
        outputs[pid] = item
      elif isinstance(item, datetime): # process set timeout
        # print(f"setting timeout of pid {pid} ({item})")
        timeouts.append((pid, item))
      elif item is None: # process canceled timeout
        for i, (p, t) in enumerate(timeouts):
          if pid == p:
            # print(f"canceling timout of pid {pid} ({t})")
            del timeouts[i]
      else:
        raise RuntimeWarning("Invalid item in queue", item)

    timeouts.sort(key=lambda x: x[1])
    time.sleep(TERMINATION_DELAY.total_seconds()) # give timeouted processes enough time to report their output
    for pid, t in timeouts:
      if t > datetime.now():
        break
      for i, p in enumerate(running):
        if pid == p.pid:
          print(f"process {pid} exceeded its timeout {t} by {datetime.now() - t}, terminating")
          p.terminate()
    
    for i, p in enumerate(running):
      if not p.is_alive():
        print(f"process {p.pid} finished with exit code {p.exitcode}")
        exitcodes[p.pid] = p.exitcode
        p.join()
        del running[i]
  
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

  parser = argparse.ArgumentParser(description=TESTSUITE_DESCRIPTION)
  parser.add_argument('-p', '--processes', help="maximum number of processes to use in parallel", type=int, default=os.cpu_count())
  parser.add_argument('script', help="The script file to test. MUST end in '.py' (without quotes)!", nargs='+', type=filetype)
  return parser.parse_args()

def runtest(index: int, scriptpath: Path, queue: multiprocessing.Queue):
  output = io.StringIO()
  sys.stdout = sys.stderr = output
  pid = multiprocessing.current_process().pid
  queue.put((pid, index))

  def reportoutput():
    while True:
      queue.put((pid, output.getvalue()))
      time.sleep(OUTPUT_REPORT_DELAY.total_seconds())
  t = threading.Thread(target=reportoutput, daemon=True)
  t.start()

  def testwrapper(lib, _):
    queue.put((pid, None))
    test(lib, scriptpath)
  queue.put((pid, datetime.now() + LIBRARY_LOAD_TIMEOUT))
  try:
    testscript(scriptpath, testwrapper)
  except Exception as e:
    traceback.print_exception(*sys.exc_info())
  queue.put((pid, output.getvalue()))

# https://stackoverflow.com/a/52328080/2808520
def testscript(scriptpath: Path, test: Callable):
  '''
  Runs some tests with the given script.
  '''
  assert(scriptpath.name.endswith('.py')) # thwart ModuleNotFoundError 
  sys.path.insert(0, str(scriptpath.parent))
  imported_library = import_module(scriptpath.name[:-3])
  test(imported_library, scriptpath)
  del imported_library
  sys.path.pop(0)

if __name__ == '__main__':
  main()