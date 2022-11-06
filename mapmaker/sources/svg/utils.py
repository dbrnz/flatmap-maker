#===============================================================================
#
#  Flatmap viewer and annotation tools
#
#  Copyright (c) 2020  David Brooks
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#
#===============================================================================

from __future__ import annotations
import math
import re
import string

#===============================================================================

# https://simoncozens.github.io/beziers.py/index.html
from beziers.cubicbezier import CubicBezier
from beziers.line import Line as BezierLine
from beziers.path import BezierPath
from beziers.point import Point as BezierPoint
from beziers.quadraticbezier import QuadraticBezier
from beziers.segment import Segment as BezierSegment

import shapely.geometry
from shapely.geometry.base import BaseGeometry

#===============================================================================

from mapmaker.geometry import Transform, reflect_point
from mapmaker.geometry.beziers import bezier_sample
from mapmaker.geometry.arc_to_bezier import bezier_segments_from_arc_endpoints, tuple2

from .. import PIXELS_PER_INCH

#===============================================================================

def SVG_NS(tag):
    return '{{http://www.w3.org/2000/svg}}{}'.format(tag)

XLINK_HREF = '{http://www.w3.org/1999/xlink}href'

#===============================================================================

CM_PER_INCH = 2.54
MM_PER_INCH = 10*CM_PER_INCH

POINTS_PER_INCH = 72
PICAS_PER_INCH = 6

#===============================================================================

__unit_scaling = {
    'px': 1,
    'in': PIXELS_PER_INCH,
    'cm': PIXELS_PER_INCH/CM_PER_INCH,
    'mm': PIXELS_PER_INCH/MM_PER_INCH,
    'pt': PIXELS_PER_INCH/POINTS_PER_INCH,
    'pc': PIXELS_PER_INCH/PICAS_PER_INCH,
    '%' : None,      # 1/100.0 of viewport dimension
    'em': None,      # em/pt depends on current font size
    'ex': None,      # ex/pt depends on current font size
    }

def length_as_pixels(length):
#============================
    if not isinstance(length, str):
        return length
    match = re.search(r'(.*)(em|ex|px|in|cm|mm|pt|pc|%)', length)
    if match is None:
        return float(length)
    else:
        scaling = __unit_scaling[match.group(2)]
        if scaling is None:
            raise ValueError('Unsupported units: {}'.format(length))
        return scaling*float(match.group(1))

#===============================================================================

# From https://codereview.stackexchange.com/questions/28502/svg-path-parsing

COMMANDS = set('MmZzLlHhVvCcSsQqTtAa')
COMMAND_RE = re.compile("([MmZzLlHhVvCcSsQqTtAa])")
FLOAT_RE = re.compile("[-+]?[0-9]*\.?[0-9]+(?:[eE][-+]?[0-9]+)?")

def parse_svg_path(path):
    for x in COMMAND_RE.split(path):
        if x in COMMANDS:
            yield x
        for token in FLOAT_RE.findall(x):
            if token.upper().startswith('E'):
                token = '1' + token
            yield token

#===============================================================================

# Helpers for encoding names for Adobe Illustrator

def __match_to_char(m):
#======================
    s = m[0]
    if s == '_':
        return ' '
    else:
        return chr(int(s[2:4], 16))

def adobe_decode(s):
#===================
    markup = re.sub('(_x.._)|(_)', __match_to_char, s).strip()
    numeric_suffix = re.search('( [0-9]+)$', markup)
    return markup if numeric_suffix is None else markup[0:-len(numeric_suffix[1])].strip()

def adobe_decode_markup(element):
#================================
    return adobe_decode(element.attrib.get('id', ''))

def __match_to_hex(m):
#=====================
    c = m[0]
    return (c   if c in (string.ascii_letters + string.digits) else
            '_' if c in string.whitespace else
            '_x{:02X}_'.format(ord(c)))

def adobe_encode(s, suffix=None):
#================================
    if suffix is not None:
        s = f'{s} {str(suffix)} '
    return re.sub('.', __match_to_hex, s)

#===============================================================================

def svg_markup(element):
#=======================
    if (markup := element.findtext(SVG_NS('title'), default='')) != '':
        return markup
    else:
        return adobe_decode_markup(element)

#===============================================================================

def geometry_from_svg_path(path_tokens: list[str|float], transform: Transform,
                           must_close: bool=None) -> tuple[BaseGeometry, list[BezierSegment], bool]:
    coordinates = []
    bezier_segments = []
    closed = False

    moved = False
    first_point = None
    current_point = None

    pos = 0
    while pos < len(path_tokens):
        if isinstance(path_tokens[pos], str) and path_tokens[pos].isalpha():
            cmd = path_tokens[pos]
            pos += 1
        # Else repeat previous command with new coordinates
        # with `moveTo` becoming `lineTo`
        elif cmd == 'M':
            cmd = 'L'
        elif cmd == 'm':
            cmd = 'l'

        if cmd not in ['s', 'S']:
            second_cubic_control = None
        if cmd not in ['t', 'T']:
            second_quad_control = None

        if cmd in ['a', 'A']:
            params = [float(x) for x in path_tokens[pos:pos+7]]
            pos += 7
            pt = params[5:7]
            if cmd == 'a':
                pt[0] += current_point[0]
                pt[1] += current_point[1]
            phi = math.radians(params[2])
            segs = bezier_segments_from_arc_endpoints(tuple2(*params[0:2]), phi, *params[3:5],
                                                      tuple2(*current_point), tuple2(*pt), transform)
            bezier_segments.extend(segs)
            coordinates.extend(bezier_sample(BezierPath.fromSegments(segs)))
            current_point = pt

        elif cmd in ['c', 'C', 's', 'S']:
            coords = [BezierPoint(*transform.transform_point(current_point))]
            if cmd in ['c', 'C']:
                n_params = 6
            else:
                n_params = 4
                if second_cubic_control is None:
                    coords.append(BezierPoint(*transform.transform_point(current_point)))
                else:
                    coords.append(BezierPoint(*transform.transform_point(
                        reflect_point(second_cubic_control, current_point))))
            params = [float(x) for x in path_tokens[pos:pos+n_params]]
            pos += n_params
            for n in range(0, n_params, 2):
                pt = params[n:n+2]
                if cmd.islower():
                    pt[0] += current_point[0]
                    pt[1] += current_point[1]
                if n == (n_params - 4):
                    second_cubic_control = pt
                coords.append(BezierPoint(*transform.transform_point(pt)))
            bz = CubicBezier(*coords)
            bezier_segments.append(bz)
            coordinates.extend(bezier_sample(bz))
            current_point = pt

        elif cmd in ['l', 'L', 'h', 'H', 'v', 'V']:
            if cmd in ['l', 'L']:
                params = [float(x) for x in path_tokens[pos:pos+2]]
                pos += 2
                pt = params[0:2]
                if cmd == 'l':
                    pt[0] += current_point[0]
                    pt[1] += current_point[1]
            else:
                param = float(path_tokens[pos])
                pos += 1
                if cmd == 'h':
                    param += current_point[0]
                elif cmd == 'v':
                    param += current_point[1]
                if cmd in ['h', 'H']:
                    pt = [param, current_point[1]]
                else:
                    pt = [current_point[0], param]
            if moved:
                coordinates.append(transform.transform_point(current_point))
                moved = False
            coordinates.append(transform.transform_point(pt))
            bz = BezierLine(BezierPoint(*coordinates[-2]), BezierPoint(*coordinates[-1]))
            bezier_segments.append(bz)
            current_point = pt

        elif cmd in ['m', 'M']:
            params = [float(x) for x in path_tokens[pos:pos+2]]
            pos += 2
            pt = params[0:2]
            if first_point is None:
                # First `m` in a path is treated as `M`
                first_point = pt
            else:
                if cmd == 'm':
                    pt[0] += current_point[0]
                    pt[1] += current_point[1]
            current_point = pt
            moved = True

        elif cmd in ['q', 'Q', 't', 'T']:
            coords = [BezierPoint(*transform.transform_point(current_point))]
            if cmd in ['q', 'Q']:
                n_params = 4
            else:
                n_params = 2
                if second_quad_control is None:
                    coords.append(BezierPoint(*transform.transform_point(current_point)))
                else:
                    coords.append(BezierPoint(*transform.transform_point(
                        reflect_point(second_quad_control, current_point))))
            params = [float(x) for x in path_tokens[pos:pos+n_params]]
            pos += n_params
            for n in range(0, n_params, 2):
                pt = params[n:n+2]
                if cmd.islower():
                    pt[0] += current_point[0]
                    pt[1] += current_point[1]
                if n == (n_params - 4):
                    second_quad_control = pt
                coords.append(BezierPoint(*transform.transform_point(pt)))
            bz = QuadraticBezier(*coords)
            bezier_segments.append(bz)
            coordinates.extend(bezier_sample(bz))
            current_point = pt

        elif cmd in ['z', 'Z']:
            if first_point is not None and current_point != first_point:
                coordinates.append(transform.transform_point(first_point))
            closed = True
            first_point = None

        else:
            log.warning('Unknown SVG path command: {}'.format(cmd))

    if must_close == False and closed:
        raise ValueError("Shape can't have closed geometry")
    elif must_close == True and not closed:
        raise ValueError("Shape must have closed geometry")

    if closed and len(coordinates) >= 3:
        geometry = shapely.geometry.Polygon(coordinates)
    elif must_close == True and len(coordinates) >= 3:
        # Return a polygon if flagged as `closed`
        coordinates.append(coordinates[0])
        geometry = shapely.geometry.Polygon(coordinates)
    elif len(coordinates) >= 2:
        ## Warn if start and end point are ``close`` wrt to the length of the line as shape
        ## may be intended to be closed... (test with ``cardio_8-1``)
        geometry = shapely.geometry.LineString(coordinates)
    else:
        geometry = None

    if geometry is not None and not geometry.is_valid:
        if geometry.geom_type == 'Polygon':
            # Try smoothing out boundary irregularities
            geometry = geometry.buffer(20)
        if not geometry.is_valid:
            raise ValueError(f'{geometry.geom_type} geometry is invalid')

    return (geometry, bezier_segments)

#===============================================================================

