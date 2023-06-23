#!/usr/bin/python3

import argparse
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
KM_RANGE = 15
MAX_TRANSIT_DURATION_BH = 40
MAX_TRANSIT_DURATION_HEATHROW = 60

with open(os.path.join(os.path.dirname(os.path.realpath(__file__)),
                       "config.json")) as f:
    config = json.load(f)
    token = config["slack_token"]
    center_addr = config["center_addr"]
    work_addr1 = config["work_addr1"]
    work_addr2 = config["work_addr2"]
    maps_api_key = config["maps_api_key"]


sc = WebClient(token)

def directions_link(from_addr, to_addr):
    def maps_link(from_addr, to_addr):
        query_string = urlencode(
            OrderedDict(f="d",
                        saddr=from_addr,
                        daddr=to_addr,
                        dirflg="r"))

        return "http://maps.google.co.uk/?%s" % query_string

    return "<{}|{}>".format(
        maps_link(from_addr, to_addr), "maps")


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

    if epc and (epc.upper() in list("EFG")):
        return False, "EPC is too low: %s" % epc.upper()
    
    if prop['duration_1_transit'] is not None and prop['duration_1_transit'] > MAX_TRANSIT_DURATION_BH:
        return False, "too far from bush house: %s" % work_addr1

    if prop['duration_2_transit'] is not None and prop['duration_2_transit'] > MAX_TRANSIT_DURATION_HEATHROW:
        return False, "too far from heathrow: %s" % work_addr1
    
    return True, ""


def notify(property_id):
    print("Notifying about %s..." % property_id)

    def make_link(property_id):
        return ("https://www.openrent.co.uk/%s" % property_id)

    with open(property_filepath(property_id)) as f:
        prop = json.load(f)

    should_notify_, reason = should_notify(prop)
    if not should_notify_:
        print("Skipping notification: %s..." % reason)
        return

    text = ("{title} close to {location} ({walk_duration})\n<{link}>.\n\n"
            "Price: {price}\nAvailable from: {av}\nEPC: {epc}\n{has_garden}\n\n"
            "Directions to BH: {directions_to_place_1}.\nTime to BH by public transport: {time_to_place_1_transit}.\nTime to BH by bike: {time_to_place_1_bike}.\n\n"
            "Directions to Heathrow: {directions_to_place_2}.\nTime to Heathrow by public transport: {time_to_place_2_transit}.\n"
            "Description: ```{desc}```").format(
        location=prop['location'][0][0],
        walk_duration=prop['location'][0][1],
        link=make_link(property_id),
        price=prop['price'],
        desc=prop['description'][:1000],
        av=prop['available_from'],
        title=prop['title'],
        epc=prop['EPC'],
        directions_to_place_1=directions_link(prop['address'], work_addr1),
        directions_to_place_2=directions_link(prop['address'], work_addr2),
        time_to_place_1_transit=prop['duration_1_transit'],
        time_to_place_1_bike=prop['duration_1_bike'],
        time_to_place_2_transit=prop['duration_2_transit'],
        has_garden="With garden. " if prop['has_garden'] else "")
    channel = '#houses-medium'
    if prop['duration_1_transit'] is None:
        channel = '#houses-distance-none'
    elif prop['duration_1_transit'] < MAX_TRANSIT_DURATION_BH - 15:
        channel = '#houses-close'
    sc.chat_postMessage(
        channel=channel,
        text=text, 
        username='propertybot',
        icon_emoji=':new:')


def update_list(should_notify=True):
    query_string = urlencode(
        OrderedDict(term=center_addr,
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

