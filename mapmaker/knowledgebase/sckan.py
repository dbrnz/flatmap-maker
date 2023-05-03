#===============================================================================
#
#  Flatmap viewer and annotation tools
#
#  Copyright (c) 2019 - 2023  David Brooks
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

from collections import defaultdict
import json
from pathlib import Path
import sqlite3
from typing import Optional

#===============================================================================

import mapmaker.knowledgebase as knowledgebase
from mapmaker.properties.pathways import PATH_TYPE, path_type_from_phenotypes
from mapmaker.settings import settings
from mapmaker.utils import log

#===============================================================================

class AnnotatorDatabase:
    def __init__(self, flatmap_dir):
        self.__db = None
        db_name = (Path(flatmap_dir) / '..' / 'annotation.db').resolve()
        if db_name.exists():
            self.__db = sqlite3.connect(db_name)
        elif settings.get('exportNeurons') is not None:
            log.warning(f'Missing annotator database: {db_name}')

    def get_derivation(self, feature_id: str) -> list[str]:
    #======================================================
        result = []
        if self.__db is not None:
            row = self.__db.execute('''select value from annotations as a
                                        where created=(select max(created) from annotations
                                            where feature=? and property='prov:wasDerivedFrom')
                                        and a.property='prov:wasDerivedFrom' ''',
                                    (feature_id,)).fetchone()
            if row:
                result = [url for url in json.loads(row[0]) if url.startswith('http')]
        return result

#===============================================================================

class SckanNodeSet:
    def __init__(self, path_id, connectivity):
        self.__id = path_id
        self.__node_dict: dict[str, set[tuple[str, ...]]] = defaultdict(set)
        # Normalise node from tuple[str, tuple[str, ..]] to tuple[str, ..]
        # removing any duplicates
        nodes = set()
        for connection in connectivity:
            for node in connection:
                nodes.add((node[0], *node[1]))
        # Build an index with successive terms of a node's tuple
        # identifying any following terms
        for node in nodes:
            node_list = list(node)
            while len(node_list):
                if len(node_list) > 1:
                    self.__node_dict[node_list[0]].add(tuple(node_list[1:]))
                else:
                    self.__node_dict[node_list[0]] = set()
                node_list.pop(0)

    def has_connector(self, ftu: str, organ: Optional[str]=None) -> bool:
    #====================================================================
        if (node_layers := self.__node_dict.get(ftu)) is not None:
            if organ is None or len(node_layers) == 0:
                return True
            else:
                for layers in node_layers:
                    if organ in layers:
                        return True
        return organ is not None and self.__node_dict.get(organ) is not None

#===============================================================================

class SckanNeuronPopulations:
    def __init__(self, flatmap_dir):
        self.__annotator_database = AnnotatorDatabase(flatmap_dir)
        self.__sckan_path_nodes_by_type: dict[PATH_TYPE, dict[str, SckanNodeSet]] = defaultdict(dict[str, SckanNodeSet])
        self.__paths_by_id = {}
        connectivity_models = knowledgebase.connectivity_models()
        for model in connectivity_models:
            model_knowledege = knowledgebase.get_knowledge(model)
            for path in model_knowledege['paths']:
                path_knowledge = knowledgebase.get_knowledge(path['id'])
                self.__paths_by_id[path['id']] = path_knowledge
                path_type = path_type_from_phenotypes(path_knowledge.get('phenotypes', []))
                self.__sckan_path_nodes_by_type[path_type][path['id']] = SckanNodeSet(path['id'], path_knowledge['connectivity'])
        self.__unknown_connections = []

    def lookup_connection(self, neuron_id: str,
                                end_nodes: list[tuple[str, Optional[str]]],
                                intermediates: list[tuple[str, Optional[str]]],
                                path_type: PATH_TYPE) -> list[str]:
    #==========================================================================
        path_ids = []
        for path_id, node_set in self.__sckan_path_nodes_by_type[path_type].items():
            if (node_set.has_connector(*end_nodes[0])
            and node_set.has_connector(*end_nodes[1])):
                path_ids.append(path_id)
        # Keep track of neuron paths we've found (or not found)
        if len(path_ids) == 0:
            evidence = {
                'id': neuron_id,
                'endNodes': tuple(sorted(end_nodes)),
                'type': str(path_type),
                'evidence': self.__annotator_database.get_derivation(neuron_id)
            }
            if len(intermediates):
                evidence['intermediates'] = intermediates
            self.__unknown_connections.append(evidence)
        return path_ids

    def unknown_connections_with_evidence(self):
    #===========================================
        return [data for data in self.__unknown_connections if data['evidence']]

    def knowledge(self, path_id: str) -> Optional[dict]:
    #===================================================
        return self.__paths_by_id.get(path_id)

    def path_label(self, path_id: str) -> Optional[str]:
    #===================================================
        return self.__paths_by_id.get(path_id, {}).get('label')

#===============================================================================
