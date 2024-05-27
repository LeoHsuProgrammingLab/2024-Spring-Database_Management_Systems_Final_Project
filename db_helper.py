import dotenv, os
from neo4j import GraphDatabase
from tex_helper import *
import numpy as np
from utils import *

import sys
sys.path.append('semantic_search/')
from embed_extractor import LLM

class Neo4j_interface:
    def __init__(self, conn_info='credential.txt'):
        load_status = dotenv.load_dotenv(conn_info)
        if load_status is False:
            raise RuntimeError('Environment variables not loaded.')

        URI = os.getenv("NEO4J_URI")
        AUTH = (os.getenv("NEO4J_USERNAME"), os.getenv("NEO4J_PASSWORD"))
        self.driver = GraphDatabase.driver(URI, auth=AUTH)
        self.driver.verify_connectivity()
        self.llm = LLM()    

    def __del__(self):
        self.close()

    def close(self):
        self.driver.close()

    def exec_query(self, query):
        records, summary, keys = self.driver.execute_query(query, database_="neo4j")
        # print("records: ", records)
        # print("summary: ", summary)
        # print("keys: ", keys)
        return records
    
    def count_all_authors(self):
        number = self.exec_query('MATCH (n:Author) RETURN count(n)')[0]['count(n)']
        print("number of authors: ", number)
        return number
    
    def count_all_titles(self):
        number = self.exec_query('MATCH (n:Title) RETURN count(n)')[0]['count(n)']
        print("number of titles: ", number)
        return number
    
    def count_all_nodes(self):
        nodes = self.exec_query('MATCH (n) RETURN COUNT(n)')
        print("nodes: ", nodes)
        return nodes
    
    def count_all_relationships(self):
        relationships = self.exec_query('MATCH ()-[r]->() RETURN COUNT(r)')
        print("relationships: ", relationships)
        return relationships

    def count_all_outlines(self):
        outlines = self.exec_query('MATCH (o:Outline) RETURN COUNT(o)')
        print("outlines: ", outlines)
        return outlines
    
    def count_all_embeds(self):
        embeds = self.exec_query('MATCH (e:Embedding) RETURN COUNT(e)')
        print("embeds: ", embeds)
        return embeds
    
    def count_all_keywords(self):
        keywords = self.exec_query('MATCH (k:Keyword) RETURN COUNT(k)')
        print("keywords: ", keywords)
        return keywords

    def insert_a_paper(self, title, authors, abstract, content, references):
        # Construct parameter dictionary
        params = {
            "title": title,
            "outline": abstract,
            "content": content,
            **{f"author_{i}": name for i, name in enumerate(authors)},
            **{f"reference_title_{i}": ref['title'] for i, ref in enumerate(references)}
            # **{f"author_ref_{i}_{j}": ref_author for i, ref in enumerate(references) for j, ref_author in enumerate(ref['authors'])}
        }

        # Initialize query parts
        merge_authors_and_link = ""
        merge_reference_and_link = ""

        # Merge authors and create relationships
        for i, author in enumerate(authors):
            merge_authors_and_link += f"""
                MERGE (a_{i}:Author {{name: $author_{i}}})
                CREATE (a_{i})-[:publishes]->(t)
                CREATE (t)-[:published_by]->(a_{i})
            """
        
        # Merge references and create relationships
        for i, ref in enumerate(references):
            merge_reference_and_link += f"""
                MERGE (ref_{i}:Title {{content: $reference_title_{i}}})
                CREATE (t)-[:references]->(ref_{i})
                CREATE (ref_{i})-[:referenced_by]->(t)
            """
            # for j, ref_author in enumerate(ref['authors']):
            #     merge_reference_and_link += f"""
            #         MERGE (:Author {{name: $author_ref_{i}_{j}}})-[:publishes]->(:Title {{content: $reference_title_{i}}})
            #         MERGE (:Title {{content: $reference_title_{i}}})-[:published_by]->(:Author {{name: $author_ref_{i}_{j}}})
            #     """

        # Main query for inserting the paper and its components
        query = f"""
            MERGE (t:Title {{content: $title}})
            CREATE (o:Outline {{content: $outline}})
            CREATE (t)-[:summarized_in]->(o)
            CREATE (c:Content {{content: $content}})
            CREATE (t)-[:consists_of]->(c)
            CREATE (c)-[:part_of]->(t)
            {merge_authors_and_link}
            {merge_reference_and_link}
        """

        print('start inserting a paper')
        # Execute the query
        try:
            self.driver.execute_query(query, parameters_=params, database_='neo4j')
            print("Paper inserted successfully.")
        except Exception as e:
            print(f"An error occurred: {e}")

    def insert_keyword_of_a_paper(self, keywords, title):
        for keyword in keywords:
            self.driver.execute_query("""
                MATCH (t:Title {content: $title})
                MERGE (k:Keyword {content: $keyword})
                CREATE (k)-[:topic_of]->(t)
                CREATE (t)-[:has_topic]->(k)
                """,
                keyword=keyword, title=title, database_='neo4j'
            )

    def insert_embed_of_a_paper(self, embedding: np.ndarray, title: str): # top vector of the paper
        self.driver.execute_query("""
            CREATE (e:Embedding {content: $embedding})
            WITH e
            MATCH (t:Title {content: $title})
            CREATE (t)-[:has_embedding]->(e)
            CREATE (e)-[:embedding_of]->(t)
            """, 
            embedding=embedding, title=title, database_='neo4j'
        )

    def get_all_embeds(self):
        query = """
        MATCH (t:Title)-[:has_embedding]->(e:Embedding)
        RETURN t.content AS title, e.content AS embedding
        """

        records = self.exec_query(query)
        all_embeds = []
        for record in records:
            all_embeds.append((record['title'], record['embedding']))
        return all_embeds
    
    def get_k_similar_papers(self, text, k=5):
        target_embed = self.get_embedding(text)
        all_embeds = self.get_all_embeds()

        similarities = []

        for title, embed in all_embeds:
            if title == text:
                continue
            similarity = self.llm.calculate_similarity(target_embed, embed)
            similarities.append((title, similarity))
        
        similarities.sort(key=lambda x: x[1], reverse=True)
        return similarities[:k]

    def get_embedding(self, text: str):
        text = truncate_text_to_bytes(text)
        return self.llm.get_embedding(text)
    
    def get_keywords(self, abstract):
        return self.llm.get_keywords(abstract)

    def insert_document(self, path):
        with open(path, 'r') as f:
            tex = f.read()
    
        # extract nodes
        title = extract_by_label(tex, 'title')
        abstract = extract_begin_end(tex, 'abstract')[0]
        conclusion = extract_conclusion(tex)
        authors = extract_authors(tex)
        content = extract_content(tex)
        reference = extract_reference(tex)

        merge_authors_and_link = ""
        for i in range(len(authors)):
            merge_authors_and_link += """
            MERGE (author{idx}:Author {{name: '{name}'}})
            CREATE (author{idx})-[:publishes]->(title)
            CREATE (title)-[:published_by]->(author{idx}) 
            """.format(name=authors[i], idx=i)
        
        merge_reference_and_link = ""
        for i in range(len(reference)):
            merge_reference_and_link += """
            MERGE (title{idx}:Title {{content: '{title}'}})
            MERGE (title)-[:refereces]->(title{idx})
            CREATE (title{idx})-[:referenced_by]->(title)
            """.format(title=reference[i], idx=i)

        # query
        self.driver.execute_query("""
            CREATE (title:Title {content: $title})
            CREATE (outline:Outline {content: $outline})
            CREATE (title)-[:summarized_in]->(outline)
            CREATE (content:Content {content: $content})
            CREATE (title)-[:consists_of]->(content)
            CREATE (content)-[:part_of]->(title)
            """ + merge_authors_and_link + merge_reference_and_link,
            title=title, 
            outline=abstract+conclusion, 
            content=content,
            database_='neo4j'
        )
    
    def text2cypher(self, text: str):
        if 'title' in text and 'contain' not in text:
            # 限制只能是 ... title <title>
            title = text.split('title ')[1]
            title = title.replace('"', '')
            title = title.replace("'", "")
            title = title.replace(".", "")
            query = f"MATCH (n: Title) WHERE TOLOWER(n.content) = TOLOWER('{title}') RETURN n"
            result = self.exec_query(query)
        elif 'contain' in text and 'keyword' not in text:
            keywords = text.split('contain ')[1]
            keywords = keywords.replace('"', '')
            keywords = keywords.replace("'", "")
            keywords = keywords.replace(".", "")
            keywords = keywords.split(', ')
            
            result = []
            for keyword in keywords:
                query = f"MATCH (n: Title) WHERE TOLOWER(n.content) CONTAINS TOLOWER('{keyword}') RETURN n"
                result += self.exec_query(query)
        elif 'keyword' in text:
            pass

if __name__ == '__main__':
    interface = Neo4j_interface()
    # interface.exec_query('MATCH (n) DETACH DELETE n')
    # interface.insert_document('paper/AceKG.tex')
    # interface.exec_query('MATCH (n) RETURN n')
    # interface.exec_query(f"MATCH (n: Title) WHERE n.content = 'Regular Path Query Evaluation on Streaming Graphs' RETURN n")

    # text1 = "Find papers with title 'Regular Path Query Evaluation on Streaming Graphs'."
    # text2 = "Get papers with title Regular Path Query Evaluation on Streaming Graphs."
    # text3 = "Find papers that the title contain 'Query', 'Graph'."
    # interface.text2cypher(text3)

    k_papers = interface.get_k_similar_papers('I want to find something about Distributed Graph Database.')
    titles = [title for title, _ in k_papers]
    
    for title in titles:
        print(title)