import numpy as np
from io import StringIO
import os, io, random, glob
import string
import pandas as pd
from evcouplings import align
from evcouplings.compare.mapping import map_indices
from Bio import pairwise2
from Bio import SeqIO
from Bio.Align import substitution_matrices
from Bio.Data import SCOPData
from Bio.PDB import PDBParser
from Bio.PDB import is_aa

alphabet = "ACDEFGHIKLMNPQRSTVWY"


def fa_to_pd(filename):
    '''
    Read fasta file and return pandas dataframe with each row containing 
    a sequence header and sequence
    '''
    column_labels = ['header', 'Sequence']
    ali = []

    with open(filename, 'r') as fileobj:
        for seq_id, seq in align.alignment.read_fasta(fileobj):
            ali.append([seq_id, seq])

    ali = pd.DataFrame(ali, columns=column_labels)
    return ali


def write_fa(fa_table, outfile, headname, seqname):
    '''write seq table to fasta'''
    with open(outfile, 'w') as f:
        for i, row in fa_table.iterrows():
            f.write('>' + str(row[headname]) + '\n')
            f.write(row[seqname] + '\n')


def alphanumeric_index_to_numeric_index(
    n,
    alpha_to_numeric={
        a: (i + 1) / 100
        for i, a in enumerate(string.ascii_uppercase)
    }):
    '''convert pdb alphanumeric residue index to numeric index'''
    n = (''.join(c for c in n if c.isdigit())
         or None, ''.join(c for c in n if c.isalpha()) or None)

    if n[1]:
        return (int(n[0]) + alpha_to_numeric[n[1].upper()])
    else:
        return int(n[0])


def pairwise_align(seq1, seq2):
    '''Pairwise align protein sequences using biopython'''
    blosum62 = substitution_matrices.load("BLOSUM62")
    alignments = pairwise2.align.localds(seq1, seq2, blosum62, -10, -0.5)
    return (alignments[0][0], alignments[0][1])


def remap_to_target_seq(new_seq, target_seq):
    '''
    Make mapping table with residue IDs and indices for a new sequence 
    relative to a target sequence
    '''
    aligned = pairwise_align(new_seq, target_seq)
    map_table = map_indices(aligned[0], 1, len(new_seq), aligned[1], 1,
                            len(target_seq))
    return (map_table)


def remap_pdb_seq_to_target_seq(pdb_file,
                                chain_list,
                                target_seq_file,
                                alphabet='ACDEFGHIKLMNPQRSTVWY'):
    '''Remap chains from a pdb file to a target sequence'''
    target_seq = fa_to_pd(target_seq_file).Sequence.values[0]
    pdb_parser = PDBParser()
    structure = pdb_parser.get_structure("pdb", pdb_file)
    output_list = []

    for i, residue in enumerate(structure.get_residues()):

        if is_aa(residue):
            output_dict = {}
            # Convert three letter amino acid to one letter
            output_dict['pdb_res'] = SCOPData.protein_letters_3to1[
                residue.resname]
            # Grab residue number AND any insertion site labeling (11A, 11B, etc.)
            output_dict['pdb_position'] = str(residue.get_id()[1]) + \
                                      residue.get_id()[2].strip()
            output_dict['chain'] = residue.get_full_id()[2]
            output_dict['ind'] = i
            output_list.append(output_dict)

    resis = pd.DataFrame(output_list)
    resis = resis[resis.chain.isin(chain_list)]
    resis['pdb_i'] = resis.sort_values(['chain', 'ind'
                                        ]).groupby('chain').cumcount() + 1
    map_tables = []

    for chain in chain_list:
        chain_seq = ''.join(resis[resis.chain == chain].sort_values(
            ['ind']).pdb_res.tolist())
        map_table = remap_to_target_seq(chain_seq, target_seq)
        map_table = map_table.rename(columns={
            'i': 'pdb_i',
            'A_i': 'pdb_res',
            'j': 'target_i',
            'A_j': 'target_res'
        })
        map_table['chain'] = chain
        map_table = map_table[~map_table.pdb_i.isna()]
        map_table = map_table[~map_table.target_i.isna()]
        map_table['pdb_i'] = map_table.pdb_i.astype(int)
        map_table['target_i'] = map_table.target_i.astype(int)
        map_tables.append(map_table)

    map_table = pd.concat(map_tables)
    resis = resis.drop(columns=['ind'])
    map_table = map_table.merge(resis,
                                how='left',
                                on=['chain', 'pdb_i', 'pdb_res'])
    return (map_table)


def make_mut_table(seqfile, alphabet=alphabet):
    '''Make DataFrame of all single mutations for a given protein sequence'''
    samples = []
    seq = fa_to_pd(seqfile).Sequence[0]

    for i, old in enumerate(seq):
        for new in alphabet:
            if new.upper() != old.upper():
                samples.append({
                    'i': (i + 1),
                    'wt': old.upper(),
                    'mut': new.upper()
                })

    return pd.DataFrame(samples)


def remap_struct_df_to_target_seq(struct_df, chainlist, map_table):
    '''
    remap dataframe derived from protein structure (like DSSP) to target 
    sequence, given mapping table
    '''
    struct_df = struct_df[struct_df['chain'].isin(chainlist)]
    struct_df['pdb_i'] = struct_df.sort_values(
        ['chain', 'i']).groupby('chain').cumcount() + 1
    struct_df = struct_df.rename(columns={
        'i': 'pdb_numbering',
        'wt': 'pdb_res'
    })
    struct_df = struct_df.merge(map_table,
                                how='left',
                                on=['chain', 'pdb_i', 'pdb_res'])
    struct_df = struct_df.drop(columns='pdb_i')
    struct_df = struct_df.rename(columns={'target_res': 'wt', 'target_i': 'i'})

    return (struct_df)