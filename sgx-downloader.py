#!/usr/bin/env python3

import argparse
import logging
import cgi
import os
import re
import sys
import configparser
import platform

from datetime import datetime, timedelta
from progressbar import ProgressBar, Percentage, Bar, widgets
from pathlib import Path
from urllib.error import HTTPError, URLError, ContentTooShortError
from urllib.request import urlopen, urlretrieve

# Brute force :)
NOT_DOWNLOADABLE = []

LINK_PATTERN = str()

FILE_NAME = dict()


class MyProgressBar(ProgressBar):
    """Progress bar with file name text aligned on the right."""

    def __init__(self, text):
        self.pbar = None
        self.widgets = [Percentage(), " ", Bar(), " ", text]
        super().__init__(widgets=self.widgets)

    def __call__(self, block_num, block_size, total_size):
        if not self.pbar:
            if total_size < 0:
                logging.info(f"Can not read the size of {self.widgets[4]}")
                total_size = 1

            self.pbar = ProgressBar(maxval=total_size, widgets=self.widgets)
            self.pbar.start()

        downloaded = block_num * block_size
        if downloaded < total_size:
            self.pbar.update(downloaded)
        else:
            self.pbar.finish()

    def update(self, value, text=None):
        if text is not None:
            self.widgets[4] = text
        super().update(value)

###################### Config section ##################################


def _create_default_config(config, config_path):
    """Create a default config file and save it to the default location.

    Args:
        config (ConfigParser): The config object to store config options.

        config_path (str): Path to the file save default config.

    Returns:
        ConfigParser: The config that has been created.
    """
    config.add_section("BASE")
    config.set("BASE", "LINK_PATTERN",
               "https://links.sgx.com/1.0.0/derivatives-historical/%%d/%%s")
    config.set("BASE", "pivotdate", "20230516")
    config.set("BASE", "pivotorder", "5420")
    config.set("BASE", "dayformat", "%%Y%%m%%d")
    config.set("BASE", "quiet", "False")
    config.set("BASE", "output", "./downloadedData")
    config.set("BASE", "logfile", "sgx-downloader.log")
    config.set("BASE", "errorfile", "sgx-failed.txt")
    config.set("BASE", "loglevel", "INFO")
    config.set("BASE", "downloadfiles", "td,tds,tc,tcs")
    config.set("BASE", "keyfilename", "tc")
    config.set("BASE", "max_retry", "3")

    config.add_section("FILE_NAME")
    config.set("FILE_NAME", "td", "WEBPXTICK_DT.zip")
    config.set("FILE_NAME", "tds", "TickData_structure.dat")
    config.set("FILE_NAME", "tc", "TC.txt")
    config.set("FILE_NAME", "tcs", "TC_structure.dat")

    config.add_section("DAYS")
    config.set("DAYS", "day", "yesterday")
    config.set("DAYS", "start", "yesterday")
    config.set("DAYS", "end", "yesterday")

    config.add_section("NOT_DOWNLOADABLE")
    config.set("NOT_DOWNLOADABLE", "day_ids", "2725-2754,2771,2772,2873,3025,3257,3590,3591,3710,3711,3712,3848,3849,3874,4239,4766")
    with open(config_path, "w+") as config_file:
        config.write(config_file)
    return config


def _get_default_config():
    """Get the default config stored in the config file. If the config file does not exist then create a new file.

    Returns:
        ConfigParser: config object contain default config.
    """
    config = configparser.ConfigParser()
    if platform.system() == "Windows":
        config_folder = os.getenv('APPDATA')
    else:
        config_folder = os.path.expanduser('~/.config')
    config_path = os.sep.join([config_folder, "sgx-downloader.cfg"])
    if os.path.exists(config_path):
        config.read(config_path)
        return config
    return _create_default_config(config, config_path)


def _load_config():
    """Load config file which path have stored in option passed from the command line or the default path.

    Returns:
        ConfigParser: A config that is loaded from the file whose path is passed."""
    global LINK_PATTERN, FILE_NAME, NOT_DOWNLOADABLE
    config = configparser.ConfigParser()
    config.read(args.config)
    # BASE section
    LINK_PATTERN = config.get("BASE", "LINK_PATTERN")
    args.pivotdate = config.get("BASE", "pivotdate")
    args.pivotorder = config.getint("BASE", "pivotorder")
    args.dayformat = config.get("BASE", "dayformat")
    args.quiet = config.getboolean("BASE", "quiet")
    args.output = config.get("BASE", "output")
    args.logfile = config.get("BASE", "logfile")
    args.error = config.get("BASE", "errorfile")
    args.loglevel = config.get("BASE", "loglevel")
    args.file = config.get("BASE", "downloadfiles").split(',')
    args.max_retry = config.getint("BASE", "max_retry")
    args.keyfile = config.get("BASE", "keyfilename")
    # FILE_NAME section
    for id, filename in config.items('FILE_NAME'):
        FILE_NAME[id] = filename
    # DAYS section
    args.day = config.get("DAYS", "day")
    args.start = config.get("DAYS", "start")
    args.end = config.get("DAYS", "end")
    # NOT_DOWNLOADABLE section
    for day in config.get("NOT_DOWNLOADABLE", "day_ids").split(','):
        if '-' not in day:
            NOT_DOWNLOADABLE.append(int(day))
        else:
            start, end = map(int, day.split('-'))
            NOT_DOWNLOADABLE.extend(list(range(start, end + 1)))

    return config

###################### Get day section #################################


def _search_around(sign, days_delta, str_day, str_day_current_id):
    """Try to find exact day_id for the query day by searching around. This is helper funtion for _find_exact_day_id.
    
    Args:
        sign (int): Direction to search (1 for future, -1 for past).
        days_delta (int): Difference from day_id of default pivotdate.
        str_day (str): String of the query day.
        str_day_current_id: Estimated string of the query day.
        
    Returns:
        str: Best string of the query day or '' if can not found."""
    current_id = int(args.pivotorder) + sign * days_delta
    max_range = len(NOT_DOWNLOADABLE)
    id_bound = current_id + max_range * sign
    logging.debug(f"bound = {id_bound}")
    while (str_day_current_id == ''
           or (sign < 0 and str_day <= str_day_current_id)
           or (sign > 0 and str_day >= str_day_current_id)):
        str_day_current_id = _get_str_day_from_id(current_id)
        if str_day_current_id != '':
            if str_day == str_day_current_id:
                logging.debug(f"The day_id of {str_day} is {current_id}.")
                return str_day, current_id
        if (id_bound - current_id) * sign < 0:
            logging.debug(
                "Can not find the exact day_id on the web. Return the estimated value.")
            return '', id_bound - max_range * sign
        current_id += sign
    return '', int(args.pivotorder) + sign * days_delta
  

def _get_str_day_from_id(qid):
    """Get the day which have day_id = qid. Helper function for _get_day_from_web.
    
    Args:
        qid (int): The query day_id.

    Returns:
        str: String format of the day which day_id(the_day) = qid or '' if can not found.
    """
    if qid in NOT_DOWNLOADABLE or qid < 0:
        return ''
    response = urlopen(LINK_PATTERN %
                        (qid, FILE_NAME[args.keyfile]))
    content_disposition = response.info()["Content-Disposition"]
    if content_disposition is not None:
        _, params = cgi.parse_header(content_disposition)
        filename = params["filename"]
        str_day = ''.join(re.findall(r'\d+', filename))[:8]
        return str_day
    return ''


def _find_exact_day_id(day):
    """Because there are missing data (they are listed in NOT_DOWNLOADABLE by Brute force) for unknown reasons, we need to estimate the id and then use the linear search to find the exact id base on the date.

    Args:
        day (datetime): The day you want to find day_id

    Returns:
        str: String format of the day if found or empty string if not found.
        int: day_id of the day or the estimate if not found.
    """
    str_day = day.strftime(args.dayformat)
    str_day_current_id = ''
    pivotdate = datetime.strptime(args.pivotdate, "%Y%m%d")
    sign = -1 if pivotdate > day else 1

    if day >= datetime.today():
        logging.warning(
            "You are going to download data from the future and I can not do it right now.")
        return '', -1
    logging.debug(f"Finding the day_id of {day}...")

    # Try to estimate the day from day_id
    weeks = abs((pivotdate - day).days) // 7
    days_delta = weeks * 5
    pivotdate += sign * timedelta(weeks=weeks)

    while pivotdate != day:
        pivotdate += sign * timedelta(days=1)
        if pivotdate.weekday() not in [5, 6]:  # Skip Saturdays and Sundays
            days_delta += 1
    current_id = int(args.pivotorder) + sign * days_delta
    logging.debug(f"estimate {day} = {current_id}")
    try:
        # Searching around the estimated day to find the exact day.
        day_str, day_id = _search_around(
            sign, days_delta, str_day, str_day_current_id)
        if day_str != '':
            return str_day, day_id
    except HTTPError as he:
        logging.warning(he.reason)
        logging.debug(
            "Can not find the exact day_id because of HTTPError. Return the estimated value.")
        return '', int(args.pivotorder) + sign * days_delta
    except Exception as e:
        logging.warning(e)
        logging.debug(
            "Can not find the exact day_id because above Exception. Return the estimated value.")
        return '', int(args.pivotorder) + sign * days_delta
    logging.debug(f"Not found the day_id for {str_day}.")
    return '', current_id - 1

###################### Flow control section ############################


def _init_logger():
    """Initiate the root logger and an error-only logger."""
    root_logger = logging.getLogger()
    root_logger.setLevel(args.loglevel.upper())
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(name)s] %(levelname)s %(message)s"))
    if args.quiet:
        stream_handler.setLevel(logging.CRITICAL)
    else:
        stream_handler.setLevel(args.loglevel.upper())
    file_handle = logging.FileHandler(filename=args.logfile, encoding='utf8')
    file_handle.setFormatter(logging.Formatter(
        "%(asctime)s [%(name)s] %(levelname)s %(message)s"))
    file_handle.setLevel(args.loglevel.upper())

    failed = logging.getLogger("failed")
    failed.setLevel(logging.WARNING)
    failed_file = logging.FileHandler(filename=args.error, encoding='utf8')

    failed.addHandler(failed_file)
    root_logger.addHandler(file_handle)
    root_logger.addHandler(stream_handler)


def _update_option(yesterday):
    """Handle when the --update option is turned on. Try to download the latest data (data of yesterday).

    Returns:
        bool: Is the data of yesterday downloaded successfully?
    """
    logging.debug("Downloading latest data (yesterday data).")
    str_day, day_id = _find_exact_day_id(
        datetime.strptime(yesterday, "%Y%m%d"))
    metadata = {"id": day_id, "day": str_day}
    for file in args.file:
        if str_day != '':
            print(metadata)
            status = _redownload(metadata=metadata, filename=FILE_NAME[file])
            # get_file(metadata=metadata, file_name=FILE_NAME[file], create_folder=True)
        else:
            logging.error("Not found the data from yesterday.")
            status = False
    return status


def _retry_option():
    """Handle when the --retry option is set. Try to redownload files from links in the text file that contain failed downloads."""
    new_error_list = []
    success_count = 0
    with open(args.retry, "r") as error_list:
        # Read the header
        new_error_list.append(error_list.readline())
        rows = error_list.readlines()
        for row in rows:
            info = row.split('\t')
            link = info[0]
            str_day = info[1]
            day_id = re.search(r'/(\d+)/', link).group(1)
            filename = re.search(r'/([^/]+)$', link).group(1)
            metadata = {"id": int(day_id), "day": str_day}
            status = get_file(file_name=filename, metadata=metadata)
            if status == False:
                new_error_list.append(row)
            else:
                success_count += 1
        logging.debug(
            f"{len(new_error_list)-1} error(s) will be saved to {args.retry} ({success_count} success).")
        error_list.close()
    with open(args.retry, "w") as error_list:
        error_list.writelines(new_error_list)


def _day_option(yesterday):
    """Handle --day option is set. Download the data for a specific day.

    Args:
        yesterday (str): String format of yesterday.

    Returns:
        bool: Is the download successfully?
    """
    logging.debug("Downloading data for the specific day.")
    if args.day.lower() == "yesterday":
        args.day = yesterday
    str_day, day_id = _find_exact_day_id(datetime.strptime(args.day, "%Y%m%d"))
    for file in args.file:
        if str_day != '':
            metadata = {"id": day_id, "day": str_day}
            # get_file(metadata=metadata, file_name=FILE_NAME[file], create_folder=True)
            status = _redownload(metadata=metadata, filename=FILE_NAME[file])
        else:
            logging.error(f"Not found the data of {args.day}")
            status = False
    return status


def _range_option(yesterday):
    """Handle if --start --end option present. Download data for each day from START to END.

    Args:
        yesterday (str): String format of yesterday.

    Return:
        int: Number of failed downloads.
    """
    number_of_fail = 0
    if args.start.lower() != "off" and args.end.lower() != "off":
        logging.debug("Downloading a batch of data from a range of days.")
        if args.start.lower() == "yesterday":
            args.start = yesterday
        if args.end.lower() == "yesterday":
            args.end = yesterday
        number_of_fail = download_range()
    return number_of_fail


def _past_option(yesterday):
    """Handle --past option. Download data for the last N days in recors.

    Args:
        yesterday (str): String format of yesterday.

    Returns:
        int: Number of failed downloads.
    """
    logging.debug(f"Downloading data for last {args.past} day(s).")
    args.start = (datetime.today() -
                  timedelta(days=args.past + 1)).strftime("%Y%m%d")
    args.end = yesterday
    number_of_fail = download_range()
    return number_of_fail

###################### Download section ################################


def _redownload(metadata, filename):
    """Redownload a file until downloaded successfully or exceed the maximum number of try.

    Args:
        metadata (dict): Dictionary includes data for the day.
        filename (str): File name in the link.

    Returns:
        bool: Is the download successful?    
    """
    status = get_file(metadata=metadata, file_name=filename,
                      create_folder=True)
    if status == False:
        retry = 0
        while status == False and retry < args.max_retry:
            retry += 1
            logging.warning(
                f"Retry to download file '{filename}' in {metadata['day']} (retry {retry}).")
            status = get_file(file_name=filename,
                              create_folder=True, metadata=metadata)
    return status


def _retrieve_file(link, save_dir, file_name, str_day):
    remotefile = urlopen(link)
    contentdisposition = remotefile.info()["Content-Disposition"]
    if contentdisposition is not None:
        _, params = cgi.parse_header(contentdisposition)
        filename = params["filename"]
        urlretrieve(
            link, save_dir / filename,
            MyProgressBar(filename),
        )
    else:
        logging.warning(
            f"Content disposition is None. Not found '{file_name}'. Download failed.")
        logging.getLogger("failed").error(
            f"{link}\t{str_day}\tFileNotFoundError")
        return ''
    return filename

###################### Main function ###################################


def get_file(file_name=None, create_folder=True, metadata=None):
    """Download the `file_name` you want from the SGX site on the day in metadata.

    `file_name` can be in "WEBPXTICK_DT.zip", "TickData_structure.dat", "TC.txt", "TC_structure.dat" (it base on which LINK_PATTERN you use).

    Args:
        file_name (str): The file name in the link that you want to download.
        create_folder (bool): Make a new directory for the day.
        metadata (dict): Dictionary includes data for the day (id and YYYYMMDD format).

    Returns:
        bool: The state that the file is download successful or not.
    """
    day_id = metadata["id"]
    str_day = metadata["day"]
    success = True
    failed_log = logging.getLogger("failed")

    if day_id > 0:
        link = LINK_PATTERN % (day_id, file_name)
        save_dir = args.output / \
            f"{str_day}" if create_folder else Path(args.output).resolve()
        if not os.path.exists(save_dir):
            os.mkdir(save_dir)
        try:
            filename = _retrieve_file(link, save_dir, file_name, str_day)
            success = filename != ''
        except HTTPError as he:
            logging.error(
                f'HTTP response code: {he.code}, Errno: {he.errno}, {he.reason}')
            failed_log.error(
                f"{link}\t{str_day}\tHTTPError\tcode={he.code}\terrno={he.errno}")
            success = False
        except FileNotFoundError as fnfe:
            logging.error(fnfe)
            logging.info(f"File '{file_name}' not found.")
            success = False
        except ContentTooShortError as ctse:
            logging.error(f"{ctse.strerror}, {ctse.reason}")
            failed_log.error(f"{link}\t{str_day}\tContentTooShortError")
            success = False
        except URLError as ue:
            logging.error(f'URLError. Errno: {ue.errno}, {ue.reason}')
            failed_log.error(f"{link}\t{str_day}\tURLError")
            success = False
        if success:
            logging.info(f"Downloaded file: {save_dir / filename}")

    return success


def download_range():
    """Download all data from start day to end day specify in config.

    Returns:
        int: Number of files that failed to download.
    """
    number_of_fail = 0
    total_download = 0
    start_day = datetime.strptime(args.start, "%Y%m%d")
    end_day = datetime.strptime(args.end, "%Y%m%d")

    s, sid = _find_exact_day_id(start_day)
    e, eid = _find_exact_day_id(end_day)
    logging.debug(f"[Check {sid} is {start_day}, {eid} is {end_day}.]")

    if len(args.start) > len(s):
        s = str(start_day.year) + s
    day = datetime.strptime(s, "%Y%m%d") if s else start_day

    while sid <= eid:
        logging.debug(f"{day}, {sid}, {day.weekday()}")
        if day.weekday() not in [5, 6]:
            metadata = {
                "id": sid,
                "day": day.strftime(args.dayformat)
            }
            for key in args.file:
                status = _redownload(
                    metadata=metadata, filename=FILE_NAME[key])
                if status == False:
                    number_of_fail += 1
                total_download += 1
            sid += 1
        day += timedelta(days=1)
    logging.info(
        f"Batch download completed. {number_of_fail} fail ({total_download} total).")
    return number_of_fail


def run():
    """Run the program with the setting loaded from the file or command line."""
    if not os.path.exists(args.output):
        os.mkdir(args.output)

    args.output = Path(args.output).resolve()
    if not os.path.exists(args.error):
        with open(args.error, "w+") as error_file:
            error_file.write(
                "Each line contain the info of the file that failed to download with format (delimiter=tab): Link, QueryDay, ErrorType, AdditionalInfo(optional).\n")

    _init_logger()
    logging.info(
        "-------------------------------------------------------------------------")
    logging.info(f"Starting downloading job {sys.argv}.")
    yesterday = (datetime.utcnow() - timedelta(days=1)).strftime("%Y%m%d")
    # For --retry option
    if "--retry" in sys.argv or "-r" in sys.argv:
        _retry_option()
    # For --update option
    if args.update:
        _update_option(yesterday)
    # For --day option
    if '--day' in sys.argv or args.config is not None and args.day != "off":
        _day_option(yesterday)
    # For --start --end option
    if '--start' in sys.argv or '--end' in sys.argv or '-s' in sys.argv or '-e' in sys.argv or args.config is not None:
        _range_option(yesterday)
    # For --past option
    if args.past and args.past > 0:
        _past_option(yesterday)
    logging.info("End of download job.")


def _get_not_downloadable(end=None):
    blacklist = []
    if end == None:
        end = args.pivotorder
    pbar = ProgressBar(maxval=end)
    pbar.start()
    for i in range(end + 1):
        response = urlopen(LINK_PATTERN % (i, FILE_NAME[args.keyfile]))
        content_disposition = response.info()["Content-Disposition"]
        if response.code == 200 and content_disposition is None:
            blacklist.append(i)
        pbar.update(i)
    logging.info(f"BLACK_LIST: {blacklist}")
    return blacklist


if __name__ == "__main__":
    default_config = _get_default_config()
    parser = argparse.ArgumentParser(
        description="SGX derivatives data downloader")
    parser.add_argument(
        '-c',
        "--config",
        type=str,
        help="Specify the path to the config file.",
        default=None
    )
    parser.add_argument(
        '-o',
        "--output",
        type=str,
        help="Specify the directory to save data.",
        default=default_config.get("BASE", "output")
    )
    parser.add_argument(
        '-f',
        "--file",
        type=str,
        help="List of files you want to download. It can be a set of the files listed in the FILE_NAME section in the config file. Example '--file td tds tc' for download Tick Data, Tick Data structure, and Trade Cancellation and not download Trade Cancellation structure (tcs).",
        nargs='+',
        default=default_config.get("BASE", "downloadfiles").split(',')
    )
    parser.add_argument(
        '-l',
        "--logfile",
        type=str,
        help="Log file path, default is sgx-downloader.log.",
        default=default_config.get("BASE", "logfile")
    )
    parser.add_argument(
        '-E',
        "--error",
        type=str,
        help="Path to the file that stores the list of failed downloads.",
        default=default_config.get("BASE", "errorfile")
    )
    parser.add_argument(
        '-L',
        "--loglevel",
        type=str,
        help="Log level for logging file.",
        default=default_config.get("BASE", "loglevel")
    )
    parser.add_argument(
        '-n',
        "--past",
        type=int,
        help="Download data from the past N days (not N-latest records). --past 7 only return data maximum of 5 days",
    )
    parser.add_argument(
        '-m',
        "--max_retry",
        type=int,
        help="The maximum number of times try to redownload a file when it fails (max_retry >= 0). Set max_retry=0 for no automatic re-download.",
        default=default_config.get("BASE", "max_retry")
    )
    parser.add_argument(
        '-r',
        "--retry",
        type=str,
        help="Redownload files listed in ERROR. This option requires a path to the ERROR file or it will take the default.",
        const=default_config.get("BASE", "errorfile"),
        default=default_config.get("BASE", "errorfile"),
        nargs='?'
    )
    parser.add_argument(
        '-q',
        "--quiet",
        action='store_true',
        help="Turn off the verbose mode (less annoying text, the lower level will be only saved into the log file)."
    )
    parser.add_argument(
        '-u',
        "--update",
        action="store_true",
        help="Download the latest data (data from yesterday).",
    )
    parser.add_argument(
        "--day",
        type=str,
        help="Download data for a specific day.",
        default=default_config.get("DAYS", "day"),
        const="yesterday",
        nargs='?'
    )
    parser.add_argument(
        '-s',
        "--start",
        type=str,
        help="Start date of a range download job.",
        default=default_config.get("DAYS", "start"),
        required='--end' in sys.argv
    )
    parser.add_argument(
        '-e',
        "--end",
        type=str,
        help="End date of a range download job.",
        default=default_config.get("DAYS", "end")
    )
    args = parser.parse_args()

    if len(sys.argv) == 1:
        parser.print_help()
        exit(0)

    if args.config is not None:
        config = _load_config()
    else:
        LINK_PATTERN = default_config.get("BASE", "LINK_PATTERN")
        args.pivotdate = default_config.get("BASE", "pivotdate")
        args.pivotorder = default_config.getint("BASE", "pivotorder")
        args.dayformat = default_config.get("BASE", "dayformat")
        args.keyfile = default_config.get("BASE", "keyfilename")
        for id, filename in default_config.items('FILE_NAME'):
            FILE_NAME[id] = filename
        for day in default_config.get("NOT_DOWNLOADABLE", "day_ids").split(','):
            if '-' not in day:
                NOT_DOWNLOADABLE.append(int(day))
            else:
                start, end = map(int, day.split('-'))
                NOT_DOWNLOADABLE.extend(list(range(start, end + 1)))
    run()
