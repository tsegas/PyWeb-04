from bs4 import BeautifulSoup
import geocoder
import json
import pathlib
import re
import requests

INSPECTION_DOMAIN = 'http://info.kingcounty.gov'
INSPECTION_PATH = '/health/ehs/foodsafety/inspections/Results.aspx'
INSPECTION_PARAMS = {
    'Output': 'W',
    'Business_Name': '',
    'Business_Address': '',
    'Longitude': '',
    'Latitude': '',
    'City': '',
    'Zip_Code': '',
    'Inspection_Type': 'All',
    'Inspection_Start': '',
    'Inspection_End': '',
    'Inspection_Closed_Business': 'A',
    'Violation_Points': '',
    'Violation_Red_Points': '',
    'Violation_Descr': '',
    'Fuzzy_Search': 'N',
    'Sort': 'H'
}


def get_inspection_page(**kwargs):
    """
    Given a set of inspection parameters, return an inspection page.

    This function shoud:

      * accept keyword arguments for each of the possible query values
      * build a dictionary of request query parameters from incoming keywords, using INSPECTION_PARAMS as a template
      * make a request to the inspection service search page using this query
      * return the unicode-encoded content of the page
    """
    url = INSPECTION_DOMAIN + INSPECTION_PATH
    params = INSPECTION_PARAMS.copy()
    for key, val in kwargs.items():
        if key in INSPECTION_PARAMS:
            params[key] = val
    resp = requests.get(url, params=params)
    resp.raise_for_status()
    return resp.text


def parse_source(html):
    """
    Returns a BeautifulSoup object, given the html
    """
    parsed = BeautifulSoup(html, 'html5lib')
    return parsed


def load_inspection_page(name):
    """
    Given the path of a file encoded in utf8, return the
    text of that file.
    """
    file_path = pathlib.Path(name)
    return file_path.read_text(encoding='utf8')


def restaurant_data_generator(html):
    """
    Given a BeautifulSoup instance return a find_all generator
    with only the restaurant data divs.
    """
    id_finder = re.compile(r'PR[\d]+~')
    return html.find_all('div', id=id_finder)


def has_two_tds(elem):
    """
    Predicate which reports if a BeautifulSoup element is a table
    row which has exactly two tds.
    """
    is_tr = elem.name == 'tr'
    td_children = elem.find_all('td', recursive=False)
    has_two = len(td_children) == 2
    return is_tr and has_two


def clean_data(td):
    """
    Given a td, return its text, after stripping away newlines, spaces,
    colons, and dashes.
    """
    return td.text.strip(" \n:-")


def extract_restaurant_metadata(elem):
    restaurant_data_rows = elem.find('tbody').find_all(
        has_two_tds, recursive=False
    )
    rdata = {}
    current_label = ''
    for data_row in restaurant_data_rows:
        key_cell, val_cell = data_row.find_all('td', recursive=False)
        new_label = clean_data(key_cell)
        current_label = new_label if new_label else current_label
        rdata.setdefault(current_label, []).append(clean_data(val_cell))
    return rdata


def is_inspection_data_row(elem):
    is_tr = elem.name == 'tr'
    if not is_tr:
        return False
    td_children = elem.find_all('td', recursive=False)
    has_four = len(td_children) == 4
    this_text = clean_data(td_children[0]).lower()
    contains_word = 'inspection' in this_text
    does_not_start = not this_text.startswith('inspection')
    return is_tr and has_four and contains_word and does_not_start


def get_score_data(elem):
    inspection_rows = elem.find_all(is_inspection_data_row)
    samples = len(inspection_rows)
    total = 0
    high_score = 0
    average = 0
    for row in inspection_rows:
        strval = clean_data(row.find_all('td')[2])
        try:
            intval = int(strval)
        except (ValueError, TypeError):
            samples -= 1
        else:
            total += intval
            high_score = intval if intval > high_score else high_score

    if samples:
        average = total/float(samples)
    data = {
        u'Average Score': average,
        u'High Score': high_score,
        u'Total Inspections': samples
    }
    return data


def result_generator(count):
    use_params = {
        'Inspection_Start': '2/1/2013',
        'Inspection_End': '2/1/2015',
        'Zip_Code': '98101'
    }
    # html = get_inspection_page(**use_params)
    html = load_inspection_page('inspection_page.html')
    parsed = parse_source(html)
    content_col = parsed.find("td", id="contentcol")
    data_list = restaurant_data_generator(content_col)
    for data_div in data_list[:count]:
        metadata = extract_restaurant_metadata(data_div)
        inspection_data = get_score_data(data_div)
        metadata.update(inspection_data)
        yield metadata


def get_geojson(health_record):
    """
    Takes a dictionary of the form:
    {
       'Total Inspections':2,
       'Average Score':5.0,
       'Address':[ '606-B BROADWAY AVE E', 'SEATTLE, WA 98102'],
       'Phone':['(206) 324-2635'],
       'Business Category':['Seating 13-50 - Risk Category III'],
       'Longitude':['122.3206905230'],
       'Business Name':['BAIT SHOP'],
       'High Score':10,
       'Latitude':['47.6246326349']
    }

    and returns a dictionary of the form:

    {
        "properties":{
            "Total Inspections":2,
            "Business Name":"BAIT SHOP",
            "Average Score":5.0,
            "High Score":10
        },
        "bbox":[-122.3186096, 47.6194002, -122.3183964, 47.6195027],
        "type":"Feature",
        "geometry":{
            "coordinates":[-122.3206905230, 47.6246326349],
            "type":"Point"
        }
    }
    """

    address = " ".join(health_record.get('Address', ''))

    if not address:
        return None

    geocoded = geocoder.google(address)
    geojson = geocoded.geojson

    inspection_data = {}

    use_keys = (
        'Business Name', 'Average Score', 'Total Inspections', 'High Score'
    )

    for key, val in health_record.items():
        if key not in use_keys:
            continue
        if isinstance(val, list):
            val = " ".join(val)
        inspection_data[key] = val

    geojson['properties'] = inspection_data
    return geojson


if __name__ == '__main__':
    """
    The main method is responsible for saving a json encoded map of the
    following format:


    {
        "type":"FeatureCollection",
        "features": [
            {
                "properties":{
                    "Total Inspections":1,
                    "Business Name":"11TH AVENUE INN",
                    "Average Score":5.0,
                    "High Score":5
                },
                "bbox":[-122.3186096, 47.6194002, -122.3183964, 47.6195027],
                "type":"Feature",
                "geometry":{
                    "coordinates":[-122.318503, 47.6194515],
                    "type":"Point"
                }
            },
            ...
        ]
    }

    to the file my_map.json, from the first 10 restaurant score results in
    inspection_page.html.
    """

    health_record_map = {'type': 'FeatureCollection', 'features': []}

    restaurant_health_records = result_generator(10)

    for health_record in restaurant_health_records:
        geojson = get_geojson(health_record)
        health_record_map['features'].append(geojson)

    with open('my_map.json', 'w') as file_handle:
        json.dump(health_record_map, file_handle)
