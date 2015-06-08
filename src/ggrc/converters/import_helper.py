# Copyright (C) 2013 Google Inc., authors, and contributors <see AUTHORS file>
# Licensed under http://www.apache.org/licenses/LICENSE-2.0 <see LICENSE file>
# Created By: dan@reciprocitylabs.com
# Maintained By: dan@reciprocitylabs.com

import csv
import re
from flask import current_app
from StringIO import StringIO

from ggrc.converters import COLUMN_HANDLERS
from ggrc.converters import COLUMN_ORDER
from ggrc.converters import handlers
from ggrc.models.reflection import AttributeInfo


def extract_relevant_data(csv_data):
  """ Split csv data into data and metadata """
  transpose_data = zip(*csv_data[1:])
  data = zip(*transpose_data[1:])
  column_definitions = data.pop(0)
  return column_definitions, data


def get_custom_attributes_definitions(object_class):
  definitions = {}
  custom_attributes = object_class.get_custom_attribute_definitions()
  for attr in custom_attributes:
    handler = COLUMN_HANDLERS.get(attr.title, handlers.ColumnHandler)
    definitions[attr.title] = {
        "display_name": attr.display_name,
        "mandatory": attr.mandatory,
        "handler": handler,
        "validator": None,
        "default": None,
        "description": "",
    }
  return definitions


def update_definition(definition, values_dict):
  for key, value in values_dict.items():
    if key in definition:
      definition[key] = value


def get_object_column_definitions(object_class):
  definitions = {}
  custom_attr_def = {}
  aliases = AttributeInfo.gather_aliases(object_class)

  custom_attributes = aliases.pop("custom_attributes", None)
  if custom_attributes:
    custom_attr_def = get_custom_attributes_definitions(object_class)

  filtered_aliases = [(k, v) for k, v in aliases.items() if v is not None]

  for key, value in filtered_aliases:

    column = object_class.__table__.columns.get(key)

    definition = {
        "display_name": value,
        "mandatory": False if column is None else not column.nullable,
        "default": getattr(object_class, "default_{}".format(key), None),
        "validator": getattr(object_class, "validate_{}".format(key), None),
        "handler": COLUMN_HANDLERS.get(key, handlers.ColumnHandler),
        "description": "",
    }

    if type(value) is dict:
      update_definition(definition, value)

    definitions[key] = definition

  definitions.update(custom_attr_def)

  return definitions


def get_column_order(attr_list):
  """ Sort attribute list

  Attribute list should be sorted with 3 rules:
    - attributes in COLUMN_ORDER variable must be fist and in the same
      order as defined in that variable.
    - Custom Attributes are sorted alphabetically after default attributes
    - mapping attributes are sorted alphabetically and placed last
  """
  attr_set = set(attr_list)
  default_attrs = [v for v in COLUMN_ORDER if v in attr_set]
  default_set = set(default_attrs)
  other_attrs = [v for v in attr_list if v not in default_set]
  custom_attrs = [v for v in other_attrs if not v.lower().startswith("map:")]
  mapping_attrs = [v for v in other_attrs if v.lower().startswith("map:")]
  custom_attrs.sort(key=lambda x: x.lower())
  mapping_attrs.sort(key=lambda x: x.lower())
  return default_attrs + custom_attrs + mapping_attrs


def equalize_array(array):
  """ Expand all rows of 2D array to the same lenght """
  if len(array) == 0:
    return array
  max_length = max(map(len, array))
  for row in array:
    diff = max_length - len(row)
    row.extend([""]*diff)
  return array

def utf8_encode_array(array):
  """ encode 2D array to utf8 """
  return [[val.encode("utf-8") for val in line] for line in array]


def generate_csv_string(csv_data):
  output_buffer = StringIO()
  writer = csv.writer(output_buffer)

  csv_data = equalize_array(csv_data)
  csv_data = utf8_encode_array(csv_data)

  for row in csv_data:
    writer.writerow(row)

  body = output_buffer.getvalue()
  output_buffer.close()
  return body


def generate_2d_array(width, height, value=None):
  """ Generate 2D array of size width x height """
  return [[value for _ in range(width)] for _ in range(height)]



def pretty_class_name(cls):
  return " ".join(re.findall('[A-Z][^A-Z]*', cls.__name__))
