import logging
from builtins import classmethod
from elastic.search import ScanAndScroll, ElasticQuery, Search
from elastic.query import Query
from criteria.helper.criteria_manager import CriteriaManager
from elastic.elastic_settings import ElasticSettings
from criteria.helper.criteria import Criteria
from region import utils
from elastic.result import Document

logger = logging.getLogger(__name__)


class GeneCriteria(Criteria):

    ''' GeneCriteria class define functions for building gene index type within criteria index

    '''

    @classmethod
    def process_gene_criteria(cls, feature, section, config):

        if config is None:
            config = CriteriaManager().get_criteria_config()

        section_config = config[section]
        source_idx = ElasticSettings.idx(section_config['source_idx'])
        source_idx_type = section_config['source_idx_type']

        if source_idx_type is not None:
            source_idx = ElasticSettings.idx(section_config['source_idx'], idx_type=section_config['source_idx_type'])
        else:
            source_idx_type = ''

        logger.warn(source_idx + ' ' + source_idx_type)

        global gl_result_container
        gl_result_container = {}

        def process_hits(resp_json):
            hits = resp_json['hits']['hits']
            global gl_result_container
            for hit in hits:
                result_container = cls.tag_feature_to_disease(hit, section, config,
                                                              result_container=gl_result_container)
                gl_result_container = result_container
                if gl_result_container is not None:
                    print(len(gl_result_container))

        query = cls.get_elastic_query(section, config)

        ScanAndScroll.scan_and_scroll(source_idx, call_fun=process_hits, query=query)
        cls.map_and_load(feature, section, config, gl_result_container)

    @classmethod
    def cand_gene_in_study(cls, hit, section=None, config=None, result_container={}):

        result_container_ = result_container
        feature_doc = hit['_source']
        feature_doc['_id'] = hit['_id']

        genes = feature_doc['genes']
        diseases = feature_doc['diseases']
        study_id = feature_doc['study_id']
        author = feature_doc['authors'][0]
        first_author = author['name'] + ' ' + author['initials']
        print('Number of genes for study id ' + study_id + '  genes ' +
              str(len(genes)) + str(diseases) + first_author)

        result_container_populated = cls.populate_container(study_id,
                                                            first_author,
                                                            fnotes=None, genes=genes,
                                                            diseases=diseases,
                                                            result_container=result_container_)
        return result_container_populated

    @classmethod
    def cand_gene_in_region(cls, hit, section=None, config=None, result_container={}):

        feature_doc = hit['_source']
        feature_doc['_id'] = hit['_id']

        genes = []
        if 'genes' in feature_doc:
            genes = feature_doc['genes']

        region_index = ElasticSettings.idx('REGION', idx_type='STUDY_HITS')
        (region_idx, region_idx_type) = region_index.split('/')

        print(region_idx + '  ' + region_idx_type)

        gene_dict = cls.get_gene_docs_by_ensembl_id(genes, sources=['chromosome', 'start', 'stop'])

        for gene in gene_dict:
            # get position
            gene_doc = gene_dict[gene]
            print(gene_doc.__dict__)
            build = "38"  # get it from index name genes_hg38_v0.0.2 TODO
            seqid = getattr(gene_doc, "chromosome")
            start = getattr(gene_doc, "start")
            stop = getattr(gene_doc, "stop")
            # check if they overlap a region
            overlapping_region_docs = cls.fetch_overlapping_features(build, seqid, start, stop,
                                                                     idx=region_idx, idx_type=region_idx_type)
            print(len(overlapping_region_docs))
            region_docs = utils.Region.hits_to_regions(overlapping_region_docs)
            for region_doc in region_docs:
                print(region_doc.__dict__)
                region_id = getattr(region_doc, "region_id")
                region_name = getattr(region_doc, "region_name")
                diseases = getattr(region_doc, "tags")['disease']

                result_container_populated = cls.populate_container(region_id,
                                                                    region_name,
                                                                    fnotes=None, genes=[gene],
                                                                    diseases=diseases,
                                                                    result_container=result_container)
                result_container = result_container_populated

        return result_container

    @classmethod
    def populate_container(cls, fid, fname, fnotes=None, genes=None, diseases=None, result_container={}):

        result_container_ = result_container

        criteria_dict = cls.get_criteria_dict(fid, fname, fnotes)

        dis_dict = dict()
        criteria_disease_dict = {}
        for gene in genes:
            if gene is None:
                continue

            for disease in diseases:
                dis_dict[disease] = []
                if len(result_container_.get(gene, {})) > 0:

                    criteria_disease_dict = result_container_[gene]
                    criteria_disease_dict = cls.get_criteria_disease_dict(diseases, criteria_dict,
                                                                          criteria_disease_dict)

                    result_container_[gene] = criteria_disease_dict
                else:
                    criteria_disease_dict = {}
                    criteria_disease_dict = cls.get_criteria_disease_dict(diseases, criteria_dict,
                                                                          criteria_disease_dict)
                    result_container_[gene] = criteria_disease_dict

        return result_container_

    @classmethod
    def tag_feature_to_disease(cls, feature_doc, section, config, result_container={}):
        feature_class = cls.__name__
        # Get class from globals and create an instance
        m = globals()[feature_class]()
        # Get the function (from the instance) that we need to call
        func = getattr(m, section)
        result_container_ = func(feature_doc, section, config, result_container=result_container)
        return result_container_

    @classmethod
    def is_gene_in_mhc(cls, hit, section=None, config=None, result_container={}):

        feature_id = hit['_id']
        print(feature_id)
        result_container_ = cls.tag_feature_to_all_diseases(feature_id, section, config, result_container)
        return result_container_

    @classmethod
    def gene_in_region(cls, hit, section=None, config=None, result_container={}):

        try:
            padded_region_doc = utils.Region.pad_region_doc(Document(hit))
        except:
            logger.warn('Region padding error ')
            print(hit)
            return result_container

        # 'build_info': {'end': 22411939, 'seqid': '1', 'build': 38, 'start': 22326008}, 'region_id': '1p36.12_008'}
        region_id = getattr(padded_region_doc, "region_id")
        region_name = getattr(padded_region_doc, "region_name")
        build_info = getattr(padded_region_doc, "build_info")
        diseases = getattr(padded_region_doc, "tags")['disease']
        seqid = build_info['seqid']
        start = build_info['start']
        end = build_info['end']

        print('Region id ' + region_id + 'Region name ' + region_name)
        gene_index = ElasticSettings.idx('GENE', idx_type='GENE')
        elastic = Search.range_overlap_query(seqid=seqid, start_range=start, end_range=end,
                                             idx=gene_index, field_list=['start', 'stop', '_id'],
                                             seqid_param="chromosome",
                                             end_param="stop")
        result_docs = elastic.search().docs

        genes = set()
        for doc in result_docs:
            genes.add(doc.doc_id())

        print(genes)

        result_container_populated = cls.populate_container(region_id,
                                                            region_name,
                                                            fnotes=None, genes=genes,
                                                            diseases=diseases,
                                                            result_container=result_container)
        return result_container_populated

    @classmethod
    def fetch_disease_locus(cls, hits_docs):

        region_index = ElasticSettings.idx('REGIONS', idx_type='DISEASE_LOCUS')
        disease_loc_docs = []
        locus_id_set = set()
        for doc in hits_docs.docs:
                locus_id = getattr(doc, 'disease_locus')
                if locus_id not in locus_id_set:
                    locus_id_set.add(locus_id)
                    query = ElasticQuery(Query.ids([locus_id]))
                    elastic = Search(query, idx=region_index)
                    disease_loc = elastic.search().docs
                    if(len(disease_loc) == 1):
                        disease_loc_docs.append(disease_loc[0])
                    else:
                        logger.critical('disease_locus doc not found for it ' + locus_id)

        return disease_loc_docs

    @classmethod
    def get_gene_docs_by_ensembl_id(cls, ens_ids, sources=None):
        ''' Get the gene symbols for the corresponding array of ensembl IDs.
        A dictionary is returned with the key being the ensembl ID and the
        value the gene document. '''
        query = ElasticQuery(Query.ids(ens_ids), sources=sources)
        elastic = Search(query, idx=ElasticSettings.idx('GENE', idx_type='GENE'), size=len(ens_ids))
        return {doc.doc_id(): doc for doc in elastic.search().docs}
