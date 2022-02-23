"""System utilities"""
import hashlib
import inspect
import os
import re
import shutil
import signal
import stat
import string
import subprocess
from gettext import gettext as _

from gi.repository import Gio, GLib

from lutris import settings
from lutris.util.log import logger

# Home folders that should never get deleted.
PROTECTED_HOME_FOLDERS = (
    _("Documents"),
    _("Downloads"),
    _("Desktop"),
    _("Pictures"),
    _("Videos"),
    _("Pictures"),
    _("Projects"),
    _("Games")
)


def execute(command, env=None, cwd=None, log_errors=False, quiet=False, shell=False, timeout=None):
    """
        Execute a system command and return its results.

        Params:
            command (list): A list containing an executable and its parameters
            env (dict): Dict of values to add to the current environment
            cwd (str): Working directory
            log_errors (bool): Pipe stderr to stdout (might cause slowdowns)
            quiet (bool): Do not display log messages
            timeout (int): Number of seconds the program is allowed to run, disabled by default

        Returns:
            str: stdout output
    """

    # Check if the executable exists
    if not command:
        logger.error("No executable provided!")
        return ""
    if os.path.isabs(command[0]) and not path_exists(command[0]):
        logger.error("No executable found in %s", command)
        return ""

    if not quiet:
        logger.debug("Executing %s", " ".join([str(i) for i in command]))

    # Set up environment
    existing_env = os.environ.copy()
    if env:
        if not quiet:
            logger.debug(" ".join("{}={}".format(k, v) for k, v in env.items()))
        env = {k: v for k, v in env.items() if v is not None}
        existing_env.update(env)

    # Piping stderr can cause slowness in the programs, use carefully
    # (especially when using regedit with wine)
    try:
        with subprocess.Popen(
            command,
            shell=shell,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE if log_errors else subprocess.DEVNULL,
            env=existing_env,
            cwd=cwd,
            errors="replace"
        ) as command_process:
            stdout, stderr = command_process.communicate(timeout=timeout)
    except (OSError, TypeError) as ex:
        logger.error("Could not run command %s (env: %s): %s", command, env, ex)
        return ""
    except subprocess.TimeoutExpired:
        logger.error("Command %s after %s seconds", command, timeout)
        return ""
    if stderr and log_errors:
        logger.error(stderr)
    return stdout.strip()


def read_process_output(command, timeout=5):
    """Return the output of a command as a string"""
    try:
        return subprocess.check_output(
            command,
            timeout=timeout,
            encoding="utf-8",
            errors="ignore"
        ).strip()
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as ex:
        logger.error("%s command failed: %s", command, ex)
        return ""


def get_md5_hash(filename):
    """Return the md5 hash of a file."""
    md5 = hashlib.md5()
    try:
        with open(filename, "rb") as _file:
            for chunk in iter(lambda: _file.read(8192), b""):
                md5.update(chunk)
    except IOError:
        logger.warning("Error reading %s", filename)
        return False
    return md5.hexdigest()


def get_file_checksum(filename, hash_type):
    """Return the checksum of type `hash_type` for a given filename"""
    hasher = hashlib.new(hash_type)
    with open(filename, "rb") as input_file:
        for chunk in iter(lambda: input_file.read(4096), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def is_executable(exec_path):
    """Return whether exec_path is an executable"""
    return os.access(exec_path, os.X_OK)


def make_executable(exec_path):
    file_stats = os.stat(exec_path)
    os.chmod(exec_path, file_stats.st_mode | stat.S_IEXEC)


def find_executable(exec_name):
    """Return the absolute path of an executable"""
    if not exec_name:
        return None
    return shutil.which(exec_name)


def get_pid(program, multiple=False):
    """Return pid of process.

    :param str program: Name of the process.
    :param bool multiple: If True and multiple instances of the program exist,
        return all of them; if False only return the first one.
    """
    pids = execute(["pgrep", program])
    if not pids.strip():
        return
    pids = pids.split()
    if multiple:
        return pids
    return pids[0]


def kill_pid(pid):
    """Terminate a process referenced by its PID"""
    try:
        pid = int(pid)
    except ValueError:
        logger.error("Invalid pid %s")
        return
    logger.info("Killing PID %s", pid)
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        logger.error("Could not kill process %s", pid)


def python_identifier(unsafe_string):
    """Converts a string to something that can be used as a python variable"""
    if not isinstance(unsafe_string, str):
        logger.error("Cannot convert %s to a python identifier", type(unsafe_string))
        return

    def _dashrepl(matchobj):
        return matchobj.group(0).replace("-", "_")

    return re.sub(r"(\${)([\w-]*)(})", _dashrepl, unsafe_string)


def substitute(string_template, variables):
    """Expand variables on a string template

    Args:
        string_template (str): template with variables preceded by $
        variables (dict): mapping of variable identifier > value

    Return:
        str: String with substituted values
    """
    string_template = python_identifier(str(string_template))
    identifiers = variables.keys()

    # We support dashes in identifiers but they are not valid in python
    # identifiers, which is a requirement for the templating engine we use
    # Replace the dashes with underscores in the mapping and template
    variables = dict((k.replace("-", "_"), v) for k, v in variables.items())
    for identifier in identifiers:
        string_template = string_template.replace("${}".format(identifier), "${}".format(identifier.replace("-", "_")))

    template = string.Template(string_template)
    if string_template in list(variables.keys()):
        return variables[string_template]
    return template.safe_substitute(variables)


def merge_folders(source, destination):
    """Merges the content of source to destination"""
    logger.debug("Merging %s into %s", source, destination)
    # Check if dirs_exist_ok is defined ( Python >= 3.8)
    sig = inspect.signature(shutil.copytree)
    if "dirs_exist_ok" in sig.parameters:
        shutil.copytree(source, destination, symlinks=False, ignore_dangling_symlinks=True, dirs_exist_ok=True)
    else:
        shutil.copytree(source, destination, symlinks=False, ignore_dangling_symlinks=True)


def remove_folder(path):
    """Delete a folder specified by path
    Returns true if the folder was successfully removed.
    """
    if not os.path.exists(path):
        logger.warning("Non existent path: %s", path)
        return
    logger.debug("Removing folder %s", path)
    if os.path.samefile(os.path.expanduser("~"), path):
        raise RuntimeError("Lutris tried to erase home directory!")
    try:
        shutil.rmtree(path)
    except OSError as ex:
        logger.error("Failed to remove folder %s: %s (Error code %s)", path, ex.strerror, ex.errno)
        return False
    return True


def create_folder(path):
    """Creates a folder specified by path"""
    if not path:
        return
    path = os.path.expanduser(path)
    os.makedirs(path, exist_ok=True)
    return path


def list_unique_folders(folders):
    """Deduplicate directories with the same Device.Inode"""
    unique_dirs = {}
    for folder in folders:
        folder_stat = os.stat(folder)
        identifier = "%s.%s" % (folder_stat.st_dev, folder_stat.st_ino)
        if identifier not in unique_dirs:
            unique_dirs[identifier] = folder
    return unique_dirs.values()


def is_removeable(path):
    """Check if a folder is safe to remove (not system or home, ...)"""
    if not path_exists(path):
        return False

    parts = path.strip("/").split("/")
    if parts[0] in ("usr", "var", "lib", "etc", "boot", "sbin", "bin"):
        # Path is part of the system folders
        return False

    if parts[0] == "home":
        if len(parts) <= 2:
            return False
        if len(parts) == 3 and parts[2] in PROTECTED_HOME_FOLDERS:
            return False
    return True


def fix_path_case(path):
    """Do a case insensitive check, return the real path with correct case. If the path is
    not for a real file, this corrects as many components as do exist."""
    if not path or os.path.exists(path):
        # If a path isn't provided or it exists as is, return it.
        return path
    parts = path.strip("/").split("/")
    current_path = "/"
    for part in parts:
        parent_path = current_path
        current_path = os.path.join(current_path, part)
        if not os.path.exists(current_path) and os.path.isdir(parent_path):
            try:
                path_contents = os.listdir(parent_path)
            except OSError:
                logger.error("Can't read contents of %s", parent_path)
                path_contents = []
            for filename in path_contents:
                if filename.lower() == part.lower():
                    current_path = os.path.join(parent_path, filename)
                    break

    # Only return the path if we got the same number of elements
    if len(parts) == len(current_path.strip("/").split("/")):
        return current_path


def get_pids_using_file(path):
    """Return a set of pids using file `path`."""
    if not os.path.exists(path):
        logger.error("Can't return PIDs using non existing file: %s", path)
        return set()
    fuser_path = find_executable("fuser")
    if not fuser_path:
        logger.warning("fuser not available, please install psmisc")
        return set([])
    fuser_output = execute([fuser_path, path], quiet=True)
    return set(fuser_output.split())


def reverse_expanduser(path):
    """Replace '/home/username' with '~' in given path."""
    if not path:
        return path
    user_path = os.path.expanduser("~")
    if path.startswith(user_path):
        path = path[len(user_path):].strip("/")
        return "~/" + path
    return path


def path_exists(path, check_symlinks=False, exclude_empty=False):
    """Wrapper around system.path_exists that doesn't crash with empty values

    Params:
        path (str): File to the file to check
        check_symlinks (bool): If the path is a broken symlink, return False
        exclude_empty (bool): If true, consider 0 bytes files as non existing
    """
    if not path:
        return False
    if os.path.exists(path):
        if exclude_empty:
            return os.stat(path).st_size > 0
        return True
    if os.path.islink(path):
        logger.warning("%s is a broken link", path)
        return not check_symlinks
    return False


def reset_library_preloads():
    """Remove library preloads from environment"""
    for key in ("LD_LIBRARY_PATH", "LD_PRELOAD"):
        if os.environ.get(key):
            try:
                del os.environ[key]
            except OSError:
                logger.error("Failed to delete environment variable %s", key)


def get_existing_parent(path):
    """Return the 1st existing parent for a folder (or itself if the path
    exists and is a directory). returns None, when none of the parents exists.
    """
    if path == "":
        return None
    if os.path.exists(path) and not os.path.isfile(path):
        return path
    return get_existing_parent(os.path.dirname(path))


def update_desktop_icons():
    """Update Icon for GTK+ desktop manager
    Other desktop manager icon cache commands must be added here if needed
    """
    if find_executable("gtk-update-icon-cache"):
        execute(["gtk-update-icon-cache", "-tf", os.path.join(GLib.get_user_data_dir(), "icons/hicolor")], quiet=True)
        execute(["gtk-update-icon-cache", "-tf", os.path.join(settings.RUNTIME_DIR, "icons/hicolor")], quiet=True)


def get_disk_size(path):
    """Return the disk size in bytes of a folder"""
    total_size = 0
    for base, _dirs, files in os.walk(path):
        total_size += sum([
            os.stat(os.path.join(base, f)).st_size
            for f in files
            if os.path.isfile(os.path.join(base, f))
        ])
    return total_size


def get_running_pid_list():
    """Return the list of PIDs from processes currently running"""
    return [p for p in os.listdir("/proc") if p[0].isdigit()]


def get_mounted_discs():
    """Return a list of mounted discs and ISOs

    :rtype: list of Gio.Mount
    """
    volumes = Gio.VolumeMonitor.get()
    drives = []

    for mount in volumes.get_mounts():
        if mount.get_volume():
            device = mount.get_volume().get_identifier("unix-device")
            if not device:
                logger.debug("No device for mount %s", mount.get_name())
                continue

            # Device is a disk drive or ISO image
            if "/dev/sr" in device or "/dev/loop" in device:
                drives.append(mount.get_root().get_path())
    return drives


def find_mount_point(path):
    """Return the mount point a file is located on"""
    path = os.path.abspath(path)
    while not os.path.ismount(path):
        path = os.path.dirname(path)
    return path


def get_mountpoint_drives():
    """Return a mapping of mount points with their corresponding drives"""
    mounts = read_process_output(["mount", "-v"]).split("\n")
    mount_map = []
    for mount in mounts:
        mount_parts = mount.split()
        if len(mount_parts) < 3:
            continue
        mount_map.append((mount_parts[2], mount_parts[0]))
    return dict(mount_map)


def get_drive_for_path(path):
    """Return the physical drive a file is located on"""
    return get_mountpoint_drives().get(find_mount_point(path))
