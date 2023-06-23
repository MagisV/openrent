#!/usr/bin/python3

import argparse
import urllib.request
import json
from bs4 import BeautifulSoup
from urllib.parse import urlencode
from collections import OrderedDict
from get_url import parse_property_page, property_filepath
from slack_sdk import WebClient
import os
import time
from selenium import webdriver

PRICE_MAX = 3000
PRICE_MIN = 0
KM_RANGE = 3

with open(os.path.join(os.path.dirname(os.path.realpath(__file__)),
                       "config.json")) as f:
    config = json.load(f)
    token = config["slack_token"]
    work_addr1 = config["work_addr1"]

sc = WebClient(token)

def directions_link(prop):
    def maps_link(start_addr, end_addr):
        query_string = urlencode(
            OrderedDict(f="d",
                        saddr=start_addr,
                        daddr=end_addr,
                        dirflg="r"))

        return "http://maps.google.co.uk/?%s" % query_string

    start_addr = ",".join(prop['title'].split(",")[1:])

    return "Directions <{}|to {}>".format(
        maps_link(start_addr, work_addr1), work_addr1)


def links_filepath():
    outdir = os.path.dirname(os.path.realpath(__file__))
    return os.path.join(outdir, 'links.json')


def should_notify(prop):
    price = prop['price']
    title = prop['title']
    desc = prop['description']
    epc = prop['EPC']

    if price > PRICE_MAX:
        return False, "too expensive: %s > %s" % price, PRICE_MAX
    if price < PRICE_MIN:
        return False, "too cheap: %s < %s" % price, PRICE_MIN

    if "Note: This OpenRent Property Is No Longer Available For Rent" in desc:
        return False, "already let"

    if "studio" in desc.lower():
        return False, "studio"

    if "studio" in title.lower():
        return False, "studio"

    if "shared flat" in desc.lower():
        return False, "shared flat"

    if "shared flat" in title.lower():
        return False, "shared flat"

    # add rules regarding distance to heathrow

    if epc and (epc.upper() in list("EFG")):
        return False, "EPC is too low: %s" % epc.upper()

    return True, ""


def notify(property_id):
    print("Notifying about %s..." % property_id)

    def make_link(property_id):
        return ("https://www.openrent.co.uk/%s" % property_id)

    # test_response = sc.api_test()
    # print(test_response)
    # sc.api_call("channels.info", channel="1234567890")

    with open(property_filepath(property_id)) as f:
        prop = json.load(f)

    should_notify_, reason = should_notify(prop)
    if not should_notify_:
        print("Skipping notification: %s..." % reason)
        return

    text = ("{title} close to {location} ({walk_duration}): <{link}>. "
            "Price: {price}. Available from: {av}. EPC: {epc}. {has_garden}"
            "{directions}.\nDescription: ```{desc}```").format(
        location=prop['location'][0][0],
        walk_duration=prop['location'][0][1],
        link=make_link(property_id),
        price=prop['price'],
        desc=prop['description'][:1000],
        av=prop['available_from'],
        title=prop['title'],
        epc=prop['EPC'],
        directions=directions_link(prop),
        has_garden="With garden. " if prop['has_garden'] else "")

    sc.chat_postMessage(
        channel="#general",
        text=text, 
        username='propertybot',
        icon_emoji=':new:')


def update_list(should_notify=True):
    query_string = urlencode(
        OrderedDict(term=work_addr1,
                    within=str(KM_RANGE),
                    prices_min=PRICE_MIN,
                    prices_max=PRICE_MAX,
                    bedrooms_min=3,
                    bedrooms_max=3,
                    isLive="true",
                    acceptStudents="true"))

    url = ("http://www.openrent.co.uk/properties-to-rent/?%s" % query_string)

    # scroll down the page using selenium
    driver = webdriver.Firefox()
    driver.get(url)
    last_height = driver.execute_script("return document.body.scrollHeight")
    while True:
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(2)
        new_height = driver.execute_script("return document.body.scrollHeight")
        if new_height == last_height:
            break
        last_height = new_height

    soup = BeautifulSoup(driver.page_source, 'html.parser')
    if os.path.isfile(links_filepath()):
        with open(links_filepath()) as f:
            existing_links = json.load(f)
    else:
        existing_links = {}

    with open(links_filepath(), 'w') as f:
        latest_links = [x['href'][1:] for x in soup.find_all("a", class_="pli clearfix")]
        print('latest links', latest_links)
        print("Received %s property links..." % len(latest_links))
        latest_and_old = list(set(latest_links) | set(existing_links))
        json.dump(latest_and_old, f, indent=4)

    new_links = list(set(latest_links) - set(existing_links))
    print("Found %s new links!..." % len(new_links))

    for property_id in new_links:
        parse_property_page(property_id)
        if should_notify:
            notify(property_id)
        else:
            print("Found a property %s but notifications are disabled."
                  % property_id)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--nonotify", help="don't notify", action='store_true',
                        default=False)
    args = parser.parse_args()

    should_notify_ = not args.nonotify
    if not os.path.isfile(links_filepath()):
        should_notify_ = False
        print("No links.json detected. This must be the first run: not"
              " notifying about all suitable properties.")
    update_list(should_notify=should_notify_)
