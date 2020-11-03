# testpythonscript

This script provides a starting point to automate testing of other python scripts (e.g. student submissions).

## Usage

Consult `python3 test.py --help` for complete usage instructions.

You may wish to run these tests within a docker container (to make it more difficult for students to mess up your machine) with [this shell script](https://gist.github.com/SimonLammer/f863627f11221379d825f7a34d8f84c3)

### Example

Here's what happens when you run the current [`test.py`](./test.py) against [`sample_script.py`](./sample_script.py):

`python3 test.py sample_script.py`:
```
process 13119 is processing sample_script.py
process 13119 finished with exit code 0
++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++
Output of sample_script.py test (exitcode 0):
test_false (__main__.Test) ... FAIL
test_foo (__main__.Test) ... ok
test_initial_attributes (__main__.Test) ... ok
test_library_load_stdin (__main__.Test) ... ok

======================================================================
FAIL: test_false (__main__.Test)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "test.py", line 36, in inner
    func(*args, stdout, stderr, **kwargs)
  File "test.py", line 70, in test_false
    self.assertTrue(LIBRARY.return_true())
AssertionError: False is not true

----------------------------------------------------------------------
Ran 4 tests in 0.001s

FAILED (failures=1)
Completed testing sample_script.py
```
