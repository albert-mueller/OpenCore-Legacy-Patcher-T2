"""
subprocess_wrapper.py: Wrapper for subprocess module to better handle errors and output
"""
import logging
import subprocess

def run(*args, **kwargs) -> subprocess.CompletedProcess:
    """
    Basic subprocess.run wrapper.
    """
    return subprocess.run(*args, **kwargs)

def run_as_root(*args, **kwargs) -> subprocess.CompletedProcess:
    """
    Run subprocess as root using sudo instead of helper tool.
    """
    if not args or not args[0]:
        raise ValueError("No command provided")
    
    # args[0] is a list like ['/usr/sbin/diskutil', 'mount', ...]
    command = ["sudo"] + list(args[0])
    return subprocess.run(command, **kwargs)

def verify(process_result: subprocess.CompletedProcess) -> None:
    """
    Verify process result and raise exception if failed.
    """
    if process_result.returncode == 0:
        return
    log(process_result)
    raise Exception(f"Process failed with exit code {process_result.returncode}")

def run_and_verify(*args, **kwargs) -> None:
    """
    Run subprocess and verify result.
    """
    verify(run(*args, **kwargs))

def run_as_root_and_verify(*args, **kwargs) -> None:
    """
    Run subprocess as root and verify result.
    """
    verify(run_as_root(*args, **kwargs))

def log(process: subprocess.CompletedProcess) -> None:
    """
    Display subprocess error output in formatted string.
    """
    for line in generate_log(process).split("\n"):
        logging.error(line)

def generate_log(process: subprocess.CompletedProcess) -> str:
    """
    Display subprocess error output in formatted string.
    """
    output = "Subprocess failed.\n"
    output += f" Command: {process.args}\n"
    output += f" Return Code: {process.returncode}\n"
    output += f"    Standard Output:\n"
    if process.stdout:
        output += __format_output(process.stdout.decode("utf-8"))
    else:
        output += "        None\n"

    output += f"    Standard Error:\n"
    if process.stderr:
        output += __format_output(process.stderr.decode("utf-8"))
    else:
        output += "        None\n"

    return output

def __format_output(output: str) -> str:
    """
    Format output.
    """
    if not output:
        return " None\n"
    _result = "\n".join([f"        {line}" for line in output.split("\n") if line.strip()])
    if not _result.endswith("\n"):
        _result += "\n"
    return _result
