#!/usr/bin/env python3

"""
Extract enough git history to facilitate git blame and have each line correctly annotated.

Wipe all history that has no connection to the current state of the repository.

The resulting repository is a drop-in replacement for the old directory and has all history needed for typical git history use.

Usage:
  git-relevant-history [options] --source=<source_repo> --filter=<filter> --target=<target_repo>

Where git repo at <source_repo> would be processed into <target_repo>, in a way that only files starting with
<filter> would be preserved (<filter> can be a subdirectory relative to <source_repo>, or text file containing
filepaths relative to <source_repo>).


Options:
  --branch=<branch>    Branch to process on the source [default: master]
  --only-specs         Only print git filter-repo specs file as expected by git filter-repo --paths-from-file
  -h --help            show this help message and exit
  -f --force           remove <target_repo> if exists
  -g --glob            use pathlib rglob to find files. Expects a filter input file with filesNames*
  -v --verbose         print status messages

"""
import logging
import pathlib
import shutil
import subprocess
import tempfile
import typing

from docopt import docopt

log_format = "%(message)s"
logging.basicConfig(format=log_format, level=logging.DEBUG)

logger = logging.root


def build_git_filter_path_spec(git_repo: pathlib.Path, filter: str, glob_filter_list: bool = False) -> typing.List[str]:

    init_files_list = []
    if not pathlib.Path(filter).exists():
        logger.debug(f"Filter is not a file, assuming it is a subdirectory of {git_repo}")

        str_subdir = filter
        if not str_subdir.endswith('/'):
            str_subdir = str_subdir + '/'

        git_repo_subdir = git_repo / str_subdir
        if not git_repo_subdir.is_dir():
            logger.critical(f"Filter {filter} is not a file, and {git_repo_subdir} is not a directory")
            raise SystemExit(-1)
        logger.debug(f"Globbing files in {git_repo_subdir}")
        init_files_list = list(git_repo_subdir.rglob('*'))
    else:
        logger.debug(f"Filter is a file, assuming it contains paths relative to {git_repo}")
        filter_file = pathlib.Path(filter)
        with open(filter_file) as infile:
            if not glob_filter_list:
                init_files_list = [git_repo / pathlib.Path(line.strip()) for line in infile]
            else:
                paths: typing.Set[str] = set()

                filter_str: str = infile.readline().strip()
                while filter_str:
                    result = set(pathlib.Path(git_repo).rglob(filter_str))
                    paths = paths.union(result)
                    filter_str = infile.readline().strip()

                init_files_list = list(paths)

    if len(init_files_list) == 0:
        logger.critical(f"Filter {filter} did not match any files")
        raise SystemExit(-1)


    all_filter_paths = []
    all_rename_statements = []

    for strpath in init_files_list:
        path = pathlib.Path(strpath)

        if path.is_file():
            repo_path = path.relative_to(git_repo)

            logger.debug(f"Including {repo_path} with history")

            unique_paths_of_current_file = {str(repo_path)}

            git_args = ["git",
                        "-C",
                        str(git_repo),
                        "log",
                        "--pretty=format:",
                        "--name-only",
                        "--follow",
                        "--",
                        str(repo_path)]
            logger.debug(f"Calling {' '.join(git_args)}")
            try:
                gitlog = subprocess.check_output(git_args,
                                                 universal_newlines=True)

                for line in gitlog.splitlines(keepends=False):
                    if len(line) > 0:
                        unique_paths_of_current_file.add(line.strip())

                if logger.isEnabledFor(logging.DEBUG):
                    this_file_paths_newlines = '\n\t'.join(unique_paths_of_current_file)
                    logger.debug(f"\t{this_file_paths_newlines}\n")

                all_filter_paths.extend(unique_paths_of_current_file)

            except subprocess.CalledProcessError as e:
                logger.warning(f"Failed to get historical names of {repo_path}, stdout: {e.output}, stderr: {e.stderr}")
                logger.warning(f"Failed command: {' '.join(git_args)}")

    if logger.isEnabledFor(logging.DEBUG):
        all_rename_statements_newlines = '\n\t'.join(all_rename_statements)
        logger.debug(f"All renames:\n\t{all_rename_statements_newlines}")
    return all_filter_paths, init_files_list


def main():
    arguments = docopt(__doc__)
    if arguments["--verbose"]:
        logger.setLevel(logging.DEBUG)
    else:
        logger.setLevel(logging.INFO)
    
    # Check git-filter-repo is available
    try:
        subprocess.check_call(["git", "filter-repo", "--version"], stdout=subprocess.DEVNULL)
    except FileNotFoundError as e:
        logger.critical("'git filter-repo' is not available, check you have added it to your PATH")
        raise SystemExit(-1)

    source_repo = pathlib.Path(arguments["--source"]).expanduser().absolute()
    if not source_repo.is_dir():
        logger.critical(f"--source {source_repo} is not a directory")
        raise SystemExit(-1)

    if not (source_repo / ".git").is_dir():
        logger.critical(f"--source {source_repo} is missing .git subdir - it need to be root of existing git repo.")
        raise SystemExit(-1)

    filter = arguments["--filter"]

    branch = "master"
    if arguments["--branch"]:
        branch = arguments["--branch"]

    glob_filter_file = "False"
    if arguments["--glob"] or arguments["-g"]:
        glob_filter_file = True

    target_repo = pathlib.Path(arguments["--target"]).expanduser().absolute()
    if target_repo.exists() and not arguments["--only-specs"]:
        if arguments["--force"]:
            logger.info(f"Will remove existing target repo at {target_repo} to store result.")
        else:
            logger.critical(f"Target directory {target_repo} already exists. Use --force to override")
            raise SystemExit(-1)

    # logger.info(f"Will convert repo at {source_repo / subdir} into {target_repo} preserving file history")

    with tempfile.TemporaryDirectory() as str_workdir:
        workdir = pathlib.Path(str_workdir)

        workclone = workdir / "repo"
        logger.debug(f"All work would happen in fresh clone (under {workclone},"
                     " that is requirement from git-filter branch"
                     " and also protects current repo state and history.")

        workclone_cmd = ["git", "clone",
                         "--branch", branch,
                         "--single-branch",
                         "file://" + str(source_repo),
                         str(workclone)]
        logger.debug(f"Calling {' '.join(workclone_cmd)}")
        subprocess.check_call(workclone_cmd)

        filenameset, init_files_list = build_git_filter_path_spec(workclone, filter, glob_filter_file)
        filter_repo_paths_file = workdir / "filter_path_specs.txt"
        with open(filter_repo_paths_file, "w") as outfile:
            for line in filenameset:
                outfile.write(line)
                outfile.write('\n')

        logger.debug(f"Stored filter repo specs in {filter_repo_paths_file}")

        if arguments["--only-specs"]:
            logger.debug(f"Dumping contents of {filter_repo_paths_file}")
            print(filter_repo_paths_file.read_text())
            return

        filter_args = ["git",
                       "-C",
                       str(workclone),
                       "filter-repo",
                       "--paths-from-file",
                       str(filter_repo_paths_file)
                       ]
        logger.debug(f"Calling {' '.join(filter_args)}")
        subprocess.check_call(filter_args,
                              universal_newlines=True)

        wipe_all_args = ["git",
                         "-C",
                         str(workclone),
                         "rm",
                         "-rf",
                         "."
                         ]
        logger.debug(f"Calling {' '.join(wipe_all_args)}")
        subprocess.check_call(wipe_all_args,
                              universal_newlines=True)

        # Iterate over the files in the init_files_list and restore them
        # This is inefficient if a subdirectory is specified, but this way
        # it's agnostic to the filter method (subdir or list of files)
        for file in init_files_list:
            restore_subdir_args = ["git",
                                "-C",
                                str(workclone),
                                "checkout",
                                "HEAD",
                                "--",
                                file.as_posix()
                                ]
            logger.debug(f"Calling {' '.join(restore_subdir_args)}")
            subprocess.check_call(restore_subdir_args,
                                universal_newlines=True)

        check_if_repo_is_dirty_args = ["git",
                                       "-C",
                                       str(workclone),
                                       "diff-index",
                                       "--quiet",
                                       "--cached",
                                       "HEAD",
                                       "--"
                                       ]
        logger.debug(f"Calling {' '.join(check_if_repo_is_dirty_args)}")
        is_dirty = subprocess.call(check_if_repo_is_dirty_args, universal_newlines=True)

        if is_dirty:
            remove_unrelated_content_args = ["git",
                                             "-C",
                                             str(workclone),
                                             "commit",
                                             "-m",
                                             "Remove not directly related content from the repository",
                                             ]
            logger.debug(f"Calling {' '.join(remove_unrelated_content_args[:-1])} \"{remove_unrelated_content_args[-1]}\"")
            subprocess.check_call(remove_unrelated_content_args,
                              universal_newlines=True)

        logger.debug(f"Moving final result from {workclone} to {target_repo}")
        if target_repo.exists():
            shutil.rmtree(target_repo)
            shutil.move(workclone, target_repo)
            logger.info(f"Replaced {target_repo} with filtering result")
        else:
            shutil.move(workclone, target_repo)
            logger.info(f"Stored filtering result at {target_repo}")


if __name__ == '__main__':
    main()
