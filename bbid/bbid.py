#!/usr/bin/env python3
import argparse
import hashlib
import imghdr
import os
import pickle
import posixpath
import re
import signal
import socket
import threading
import time
import urllib.parse
import urllib.request
import unicodedata
from io import BytesIO


# config
socket.setdefaulttimeout(2)

output_dir = './bing'  # default output dir
tried_urls = []
image_md5s = {}
in_progress = 0
urlopenheader = {'User-Agent': 'Mozilla/5.0 (X11; Fedora; Linux x86_64; rv:94.0) Gecko/20100101 Firefox/94.0'}


def slugify(value, allow_unicode=False):
    """
    Taken from https://github.com/django/django/blob/master/django/utils/text.py
    Convert to ASCII if 'allow_unicode' is False. Convert spaces or repeated
    dashes to single dashes. Remove characters that aren't alphanumerics,
    underscores, or hyphens. Convert to lowercase. Also strip leading and
    trailing whitespace, dashes, and underscores.
    """
    value = str(value)
    if allow_unicode:
        value = unicodedata.normalize('NFKC', value)
    else:
        value = unicodedata.normalize('NFKD', value).encode('ascii', 'ignore').decode('ascii')
    value = re.sub(r'[^\w\s-]', '', value.lower())
    return re.sub(r'[-\s]+', '-', value).strip('-_')

def download(pool_sema: threading.Semaphore, img_sema: threading.Semaphore, url: str, output_dir: str, limit: int, name=''):
    global tried_urls
    global image_md5s
    global in_progress
    global urlopenheader
    if url in tried_urls:
        print('SKIP: Already checked url, skipping')
        return
    pool_sema.acquire()
    in_progress += 1
    acquired_img_sema = False
    path = urllib.parse.urlsplit(url).path
    filename = posixpath.basename(path).split('?')[0]  # Strip GET parameters from filename
    name_, ext = os.path.splitext(filename)
    
    if name:
        name_ = name
    name_ = name_.strip()[:36].strip()
    name_ = slugify(name_)
    if not ext:
        ext = '.gif'
    filename = (name_ + ext).replace('.gifv', '.gif')

    try:
        request = urllib.request.Request(url, None, urlopenheader)
        image = urllib.request.urlopen(request).read()
        imgtype = imghdr.what(BytesIO(image), image)
        if not imgtype:
            print('SKIP: Invalid image, not saving ' + filename)
            return
        filename = (name_ + ext).replace('.gifv', '.gif')

        md5_key = hashlib.md5(image).hexdigest()
        if md5_key in image_md5s:
            print('SKIP: Image is a duplicate of ' + image_md5s[md5_key] + ', not saving ' + filename)
            return

        i = 0
        while os.path.exists(os.path.join(output_dir, filename)):
            if hashlib.md5(open(os.path.join(output_dir, filename), 'rb').read()).hexdigest() == md5_key:
                print('SKIP: Already downloaded ' + filename + ', not saving')
                return
            i += 1
            filename = "%s-%d%s" % (name_, i, ext)

        image_md5s[md5_key] = filename

        img_sema.acquire()
        acquired_img_sema = True
        if limit is not None and len(tried_urls) >= limit:
            return

        imagefile = open(os.path.join(output_dir, filename), 'wb')
        imagefile.write(image)
        imagefile.close()
        print(" OK : " + filename)
        tried_urls.append(url)
    except Exception as e:
        print("FAIL: " + filename, str(e))
    finally:
        pool_sema.release()
        if acquired_img_sema:
            img_sema.release()
        in_progress -= 1


def fetch_images_from_keyword(pool_sema: threading.Semaphore, img_sema: threading.Semaphore, keyword: str,
                              output_dir: str, filters: str, limit: int):
    global tried_urls
    global image_md5s
    global in_progress
    global urlopenheader
    current = 0
    last = ''
    while True:
        time.sleep(0.1)

        request_url = 'https://www.bing.com/images/async?q=' + urllib.parse.quote_plus(keyword) + '&first=' + str(
            current) + '&count=35&qft=' + ('' if filters is None else filters)
        request = urllib.request.Request(request_url, None, headers=urlopenheader)
        response = urllib.request.urlopen(request)
        html = response.read().decode('utf8')
        with open('html.html', 'w', encoding='utf8') as f:
            f.write(html)
        # links = re.findall('murl&quot;:&quot;(.*?)&quot;', html)
        from bs4 import BeautifulSoup
        import json
        soup = BeautifulSoup(html, 'html.parser')
        alist = soup.select('div.imgpt > a[m]')
        metas = [json.loads(a['m']) for a in alist]
        try:
            if metas[-1] == last:
                return
            for index, meta in enumerate(metas):
                link = meta['murl']
                name = meta['desc'] + ' - ' + meta['t']
                if limit is not None and len(tried_urls) >= limit:
                    exit(0)
                t = threading.Thread(target=download, args=(pool_sema, img_sema, link, output_dir, limit),
                        kwargs={'name': name}
                )
                t.start()
                current += 1
            last = metas[-1]
        except IndexError:
            print('FAIL: No search results for "{0}"'.format(keyword))
            return


def backup_history(*args):
    global output_dir
    global tried_urls
    global image_md5s
    global in_progress
    global urlopenheader
    download_history = open(os.path.join(output_dir, 'download_history.pickle'), 'wb')
    pickle.dump(tried_urls, download_history)
    copied_image_md5s = dict(
        image_md5s)  # We are working with the copy, because length of input variable for pickle must not be changed during dumping
    pickle.dump(copied_image_md5s, download_history)
    download_history.close()
    print('history_dumped')
    if args:
        exit(0)


def main():
    global output_dir
    global tried_urls
    global image_md5s
    global in_progress
    global urlopenheader
    parser = argparse.ArgumentParser(description="""Bing image bulk downloader
    https://github.com/FarisHijazi/Bulk-Bing-Image-downloader
    https://github.com/ostrolucky/Bulk-Bing-Image-downloader (original author)
    """)
    parser.add_argument('search_string', nargs="+", help='Keyword to search')
    parser.add_argument('-f', '--search-file', action='store_true', help='use search-string as a path to a file containing search strings line by line',
                        required=False)
    parser.add_argument('-o', '--output', help='Output directory', required=False)
    parser.add_argument('-a', '--adult-filter-off', help='Disable adult filter', action='store_true', required=False)
    parser.add_argument('-g', '--animated-gif', help='Disable adult filter', action='store_true', required=False)
    parser.add_argument('--filters',
                        help='Any query based filters you want to append when searching for images, e.g. +filterui:license-L1', default='',
                        required=False)
    parser.add_argument('--limit', help='Make sure not to search for more than specified amount of images.',
                        required=False, type=int)
    parser.add_argument('-t', '--threads', help='Number of threads', type=int, default=20)
    args = parser.parse_args()
    print(vars(args))
    args.search_string = ' '.join(args.search_string)

    if args.output:
        output_dir = args.output
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
    output_dir_origin = output_dir
    if args.animated_gif:
        args.filters += '+filterui:photo-animatedgif'
    signal.signal(signal.SIGINT, backup_history)
    try:
        download_history = open(os.path.join(output_dir, 'download_history.pickle'), 'rb')
        tried_urls = pickle.load(download_history)
        image_md5s = pickle.load(download_history)
        download_history.close()
    except (OSError, IOError):
        tried_urls = []
    if args.adult_filter_off:
        urlopenheader['Cookie'] = 'SRCHHPGUSR=ADLT=OFF'
    pool_sema = threading.BoundedSemaphore(args.threads)
    img_sema = threading.Semaphore()

    
    if not args.search_file:
        keyword = args.search_string
        output_sub_dir = os.path.join(output_dir_origin, keyword.strip().replace(' ', '_'))
        os.makedirs(output_sub_dir, exist_ok=True)
        fetch_images_from_keyword(pool_sema, img_sema, keyword, output_sub_dir, args.filters, args.limit)
    else:
        try:
            inputFile = open(args.search_string)
        except (OSError, IOError):
            print("FAIL: Couldn't open file {}".format(args.search_string))
            exit(1)
        for keyword in inputFile.readlines():
            output_sub_dir = os.path.join(output_dir_origin, keyword.strip().replace(' ', '_'))
            if not os.path.exists(output_sub_dir):
                os.makedirs(output_sub_dir)
            fetch_images_from_keyword(pool_sema, img_sema, keyword, output_sub_dir, args.filters, args.limit)
            backup_history()
            time.sleep(10)
        inputFile.close()


if __name__ == "__main__":
    main()
