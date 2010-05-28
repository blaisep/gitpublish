from docutils.core import publish_string
from xml.etree.ElementTree import XML, Element, SubElement, ElementTree
import os
import hashlib
from subprocess import Popen, PIPE
import codecs
import sys
import json

class Document(object):
    def __init__(self, basepath, path):
        self.path = os.path.join(basepath, path)
        ifile = codecs.open(self.path, 'r', 'utf-8')
        try:
            self.rest = ifile.read()
        finally:
            ifile.close()
        xhtml = publish_string(rest, writer_name='xml')
        x = XML(xhtml) # parse the XML text
        self.title = x.find('title').text #extract its title

    def __str__(self):
        return self.rest

    def write(self, rest):
        ifile = codecs.open(self.path, 'w', 'utf-8')
        try:
            ifile.write(rest)
        finally:
            ifile.close()
        self.rest = rest

def import_plugin(remoteType):
    'get Repo class from plugin/<remoteType>.py'
    try:
        mod = __import__('plugin.' + remoteType, globals(), locals(), ['Repo'])
    except ImportError:
        raise ImportError('plugin %s not found, or missing Repo class!')
    return mod.Repo


def unicode_safe_hash(s):
    'converts to utf8 before hashing, to avoid hashlib crash on unicode characters'
    e = codecs.getencoder('utf8')
    s2, n = e(s)
    return hashlib.sha1(s2).hexdigest()

class DocMap(object):
    def __init__(self):
        self.revDict = {} # map from remote docID to attribute dictionary
        self.dict = {} # map from gitpubPath to attribute dictionary

    def init_from_file(self, path):
        'initialize mapping from saved json file'
        ifile = open(path)
        try:
            d = json.load(ifile)
            remoteType = d['remoteType']
            repoArgs = d['repoArgs']
            self.dict = d['docDict']
            self.revDict = d['revDict']
        finally:
            ifile.close()
        return remoteType, repoArgs

    ## def init_from_repo(self, path, remoteType, repoArgs, docDict):
    ##     'initialize empty mapping from remote repo'
    ##     for docID, d2 in docDict.items():
    ##         d2['gitpubID'] = docID
    ##         d2['gitpubPath'] = ''
    ##         self.revDict[docID] = d2
    ##     self.save_file(path, remoteType, repoArgs)

    def save_file(self, path, remoteType, repoArgs):
        'save dict and revDict to our json file'
        d = dict(remoteType=remoteType, repoArgs=repoArgs, docDict=self.dict,
                 revDict=self.revDict)
        ifile = open(path, 'w')
        try:
            json.dump(d, ifile, sort_keys=True, indent=4)
            print >>ifile # make sure file ends in newline
        finally:
            ifile.close()

    def init_from_file_xml(self, path):
        'initialize mapping from saved XML file'
        t = ElementTree(file=path)
        root = t.getroot()
        remoteType = root.get('remoteType')
        repoArgs = {}
        for k,v in root.items():
            repoArgs[k] = v
        del repoArgs['remoteType']
        repoArgs = repoArgs
        for e in t.getiterator('doc'):
            d = e.attrib.copy() # get a copy of attribute dict
            try:
                self.dict[d['gitpubPath']] = d
            except KeyError:
                pass
            self.revDict[d['gitpubID']] = d
        return remoteType, repoArgs

    def save_file_xml(self, path, remoteType, repoArgs):
        'save dict and revDict to our xhtml file'
        repoDict = repoArgs.copy()
        repoDict['remoteType'] = remoteType
        root = Element('remote', repoDict)
        root.text = '\n' # keep each doc on separate line
        l = self.dict.keys()
        l.sort() # keep the entries in standard order
        for gitpubPath in l:
            d = {}
            for k, v in self.dict[gitpubPath].items():
                d[k] = str(v) # xml can only save string objects?
            e = SubElement(root, 'doc', d)
            e.tail = '\n' # keep each doc on separate line
        l = self.revDict.keys()
        l.sort() # keep the entries in standard order
        for gitpubID in l:
            docDict = self.revDict[gitpubID]
            try:
                if docDict['gitpubPath'] in self.dict:
                    continue # don't duplicate the same entry
            except KeyError:
                pass
            d = {}
            for k, v in docDict.items():
                d[k] = str(v) # xml can only save string objects?
            e = SubElement(root, 'doc', d)
            e.tail = '\n' # keep each doc on separate line
        t = ElementTree(root)
        t.write(path)

    def copy(self):
        'return a copy of this docmap'
        m = self.__class__()
        m.revDict.update(self.revDict)
        m.dict.update(self.dict)
        return m

    def __setitem__(self, gitpubPath, docDict):
        'add mapping for a local document, to a dict of doc-attributes'
        docDict['gitpubPath'] = gitpubPath
        self.dict[gitpubPath] = docDict
        try:
            self.revDict[docDict['gitpubID']] = docDict
        except KeyError: # document not yet published in remote, ok
            pass

    def __delitem__(self, gitpubPath):
        'delete mapping for a local document, to delete it from remote repo'
        try:
            del self.revDict[self.dict['gitpubID']]
        except KeyError: # document not yet published in remote, ok
            pass
        del self.dict[gitpubPath]

    def delete_remote_mapping(self, gitpubID):
        'delete mapping associated with a remote doc ID'
        try:
            del self.dict[self.revDict[gitpubID]['gitpubPath']]
        except KeyError:
            pass
        del self.revDict[gitpubID]

    def __sub__(self, oldmap):
        'get analysis of doc differences vs. oldmap'
        return DocMapDiff(self, oldmap)


class DocMapDiff(object):
    '''Records the diff between two DocMap objects.
    Sets 3 attributes:
    newDocs: gitpubPath present newmap.dict but not oldmap.dict
    changedDocs: gitpubPath present in newmap.dict but gitpubID or gitpubHash changed
    (or missing) in oldmap.dict
    deletedDocs: gitpubID present in oldmap.revDict but not newmap.revDict'''
    def __init__(self, newmap, oldmap):
        self.newmap = newmap
        self.oldmap = oldmap
        newDocs = []
        deletedDocs = []
        changedDocs = []
        for k in newmap.dict:
            if k not in oldmap.dict:
                newDocs.append(k)
            else:
                try:
                    if newmap.dict[k]['gitpubHash'] != oldmap.dict[k]['gitpubHash'] \
                           or newmap.dict[k]['gitpubID'] != oldmap.dict[k]['gitpubID']:
                        raise KeyError
                except KeyError:
                    changedDocs.append(k)
        for k in oldmap.revDict:
            if k not in newmap.revDict:
                deletedDocs.append(k)
        self.newDocs = newDocs
        self.deletedDocs = deletedDocs
        self.changedDocs = changedDocs


class Remote(object):
    def __init__(self, name, basepath, remoteType=None, repoArgs=None,
                 importDir='%s-import'):
        self.name = name
        self.basepath = basepath
        self.importDir = importDir
        self.path = os.path.join(basepath, '.gitpub', name + '.json')
        if not os.path.isdir(os.path.join(basepath, '.gitpub')): # create dir if needed
            os.mkdir(os.path.join(basepath, '.gitpub'))
        self.docmap = DocMap()
        try:
            remoteType, repoArgs = self.docmap.init_from_file(self.path)
            newRemote = False
        except IOError:
            newRemote = True
        klass = import_plugin(remoteType)
        self.repo = klass(**repoArgs)
        self.remoteType = remoteType
        self.repoArgs = repoArgs
        ## if newRemote:
        ##     try:
        ##         self.fetch()
        ##     except ValueError:
        ##         docDict = self.repo.list_documents()
        ##         self.docmap.init_from_repo(self.path, remoteType, repoArgs,
        ##                                    docDict)

    def save_doc_map(self):
        self.docmap.save_file(self.path, self.remoteType, self.repoArgs)
                
    def push(self, newmap):
        diff = newmap - self.docmap # analyze doc map changes
        for gitpubPath in diff.newDocs: # publish new docs on remote repo
            newdoc = Document(self.basepath, gitpubPath)
            docDict = newmap.dict[gitpubPath].copy()
            docDict['gitpubHash'] = unicode_safe_hash(newdoc.rest)
            gitpubID = self.repo.new_document(newdoc, **docDict)
            docDict['gitpubID'] = gitPubID
            self.docmap[gitpubPath] = docDict
        for gitpubPath in diff.changedDocs: # update changed docs on remote repo
            newdoc = Document(self.basepath, gitpubPath)
            docDict = newmap.dict[gitpubPath].copy()
            docDict['gitpubHash'] = unicode_safe_hash(newdoc.rest)
            self.repo.set_document(docDict['gitpubID'], newdoc, **docDict)
            self.docmap[gitpubPath] = docDict
            
        for gitpubID in diff.deletedDocs: # remove deleted docs from remote repo
            self.repo.delete_document(gitpubID)
            self.docmap.delete_remote_mapping(gitpubID)

    def fetch(self):
        'retrieve docs from remote, save changed docs and return them as list'
        importDir = os.path.join(self.basepath, self.importDir % self.name)
        if not os.path.isdir(importDir): # create dir if needed
            os.mkdir(importDir)
        try:
            self.repo.get_document
        except AttributeError:
            raise ValueError('this remote does not support fetch!')
        docDict = self.repo.list_documents()
        l = []
        for gitpubID, d in docDict.items():
            gitpubPath = self.import_doc(gitpubID, importDir)
            if gitpubPath:
                l.append(gitpubPath)
        return l

    def import_doc(self, gitpubID, importDir):
        'retrieve the specified doc from the remote repo, save to importDir'
        try: # use existing file mapping if present
            gitpubPath = self.docmap.revDict['gitpubPath']
            path = os.path.join(self.basepath, gitpubPath)
        except KeyError: # use default import path
            path = os.path.join(importDir, gitpubID + '.rst')
            gitpubPath = relpath(path, self.basepath)
        try:
            rest, d = self.repo.get_document(gitpubID)
        except StandardError:
            print >>sys.stderr, 'failed to get document %s.  Conversion error? Skipping' % gitpubID
            return None
        docDict = dict(gitpubPath=gitpubPath, gitpubID=gitpubID)
        try: # compare hash codes if present
            docDict['gitpubHash'] = d['gitpubHash'] 
            i = rest.find('gitpubHash=')
            if i >= 0: # make sure old hashcode doesn't sneak into ReST text
                rest = rest[:i] + rest[i+11:]
        except KeyError: # new content.  Save its hash value
            docDict['gitpubHash'] = unicode_safe_hash(rest)
        try:
            if docDict['gitpubHash'] == self.docmap.revDict[gitpubID]['gitpubHash']:
                return None # matches existing content, no need to update
        except KeyError:
            pass
        ifile = codecs.open(path, 'w', 'utf-8')
        try:
            ifile.write(rest)
        finally:
            ifile.close()
        self.docmap[gitpubPath] = docDict
        return gitpubPath


class TrackingBranch(object):
    def __init__(self, name, localRepo, branchName=None, doFetch=True, **kwargs):
        '''create the branch if not present'''
        if branchName is None:
            branchName = '/'.join(('gitpub', name, 'master'))
        self.branchName = branchName
        self.localRepo = localRepo
        if branchName not in localRepo.branches:
            localRepo.branch(branchName) # create new branch
        self.remote = Remote(name, localRepo.basepath, **kwargs)
        if doFetch:
            self.fetch()

    def push(self, newmap):
        'push changes to remote and commit map changes'
        self.remote.push(newmap) # actually send the changes to the remote
        self.commit(message='publish doc changes to remote', fromStage=False)

    def get_stage(self):
        'return temporary docmap where we can add changes before committing them'
        try:
            docmap = self.stage
        except AttributeError:
            docmap = self.stage = self.remote.docmap.copy()
        return docmap
        
    def add(self, path, **docDict):
        'add a file to be staged for next commit'
        gitpubPath = relpath(path, self.localRepo.basepath)
        docmap = self.get_stage()
        docmap[gitpubPath] = docDict

    def rm(self, path):
        'stage a file to be deleted in next commit'
        gitpubPath = relpath(path, self.localRepo.basepath)
        docmap = self.get_stage()
        del docmap[gitpubPath]

    def commit(self, message, fromStage=True, repoState=None):
        'commit map changes to our associated tracking branch in the local repo'
        if fromStage:
            try:
                docmap = self.stage
            except AttributeError:
                raise AttributeError('no changes to commit')
        if repoState is None:
            repoState = localRepo.push_state()
            self.localRepo.checkout(self.branchName)
        if fromStage:
            self.remote.docmap = docmap
        self.remote.save_doc_map()
        self.localRepo.add(self.remote.path)
        self.localRepo.commit(message=message)
        repoState.pop()
        if fromStage:
            del self.stage # moved this docmap to self.remote...

    def fetch(self):
        'fetch latest state from remote, and commit any changes in this branch'
        repoState = self.localRepo.push_state()
        self.localRepo.checkout(self.branchName)
        newdocs = self.remote.fetch()
        for gitpubPath in newdocs:
            self.localRepo.add(os.path.join(self.localRepo.basepath, gitpubPath))
        self.commit('fetch from remote', False, repoState)

try:
    relpath = os.path.relpath # python 2.6+
except AttributeError: # for earlier python versions
    def relpath(path, basepath=None):
        if basepath is None:
            basepath = os.getcwd()
        path = os.path.abspath(path)
        if path.startswith(basepath):
            return path[len(basepath) + 1:]
        else:
            raise ValueError('path not inside basepath!')
    

def run_subprocess(args, errmsg):
    'raise OSError if nonzero exit code'
    p = Popen(args)
    p.wait()
    if p.returncode:
        raise OSError(errmsg % p.returncode)


class GitRepoState(object):
    def __init__(self, repo):
        self.repo = repo
        self.branch = repo.branch()

    def pop(self):
        self.repo.checkout(self.branch)
        
class GitRepo(object):
    def __init__(self, basepath):
        'basepath should be top of the git repository, i.e. dir containing .git dir'
        self.basepath = basepath
        self.branches = self.list_branches()

    def checkout(self, branchname):
        'git checkout <branchname>'
        run_subprocess(('git', 'checkout', branchname), 'git checkout error %d')
        self.branches = self.list_branches()

    def add(self, path):
        'git add <path>'
        path = relpath(path) # relative to current directory
        run_subprocess(('git', 'add', path), 'git add error %d')

    def commit(self, message):
        run_subprocess(('git', 'commit', '-m', message), 'git commit error %d')

    def branch(self, branchname=None):
        'create new branch, or list existing branches, with current branch first'
        if branchname:
            run_subprocess(('git', 'branch', branchname), 'git branch error %d')
            self.branches.append(branchname)
        else: # get the current branch name
            return self.list_branches()[0]

    def list_branches(self):
        'list existing branches, with current branch first'
        l = Popen(["git", "branch"], stdout=PIPE).communicate()[0].split('\n')[:-1]
        l.sort(reverse=True) # force starred branch to be first
        return [s[2:] for s in l]

    def push_state(self):
        'get a state object representing current git repo state'
        return GitRepoState(self)
    

