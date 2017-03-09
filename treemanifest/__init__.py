# __init__.py
#
# Copyright 2016 Facebook, Inc.
#
# This software may be used and distributed according to the terms of the
# GNU General Public License version 2 or any later version.
"""
The treemanifest extension is to aid in the transition from flat manifests to
treemanifests. It has a client portion that's used to construct trees during
client pulls and commits, and a server portion which is used to generate
tree manifests side-by-side normal flat manifests.

Configs:

    ``treemanifest.server`` is used to indicate that this repo can serve
    treemanifests
"""

"""allows using and migrating to tree manifests

When autocreatetrees is enabled, you can limit which bookmarks are initially
converted to trees during pull by specifying `treemanifest.allowedtreeroots`.

    [treemanifest]
    allowedtreeroots = master,stable

Enabling `treemanifest.usecunionstore` will cause the extension to use the
native implementation of the datapack stores.

    [treemanifest]
    usecunionstore = True

"""

from mercurial import (
    changegroup,
    cmdutil,
    error,
    extensions,
    localrepo,
    manifest,
    mdiff,
    revlog,
    scmutil,
    store,
    util,
)
from mercurial.i18n import _
from mercurial.node import bin, nullid

from remotefilelog.contentstore import unioncontentstore
from remotefilelog.datapack import datapackstore, mutabledatapack
from remotefilelog.historypack import historypackstore, mutablehistorypack
from remotefilelog import shallowutil
import cstore

import struct

cmdtable = {}
command = cmdutil.command(cmdtable)

PACK_CATEGORY='manifests'

def extsetup(ui):
    extensions.wrapfunction(changegroup.cg1unpacker, '_unpackmanifests',
                            _unpackmanifests)
    extensions.wrapfunction(revlog.revlog, 'checkhash', _checkhash)

    wrappropertycache(localrepo.localrepository, 'manifestlog', getmanifestlog)

def reposetup(ui, repo):
    wraprepo(repo)

def wraprepo(repo):
    if not isinstance(repo, localrepo.localrepository):
        return

    if not repo.ui.configbool('treemanifest', 'server'):
        clientreposetup(repo)

def clientreposetup(repo):
    repo.name = repo.ui.config('remotefilelog', 'reponame')
    if not repo.name:
        raise error.Abort(_("remotefilelog.reponame must be configured"))

    try:
        extensions.find('fastmanifest')
    except KeyError:
        raise error.Abort(_("cannot use treemanifest without fastmanifest"))

    usecdatapack = repo.ui.configbool('remotefilelog', 'fastdatapack')

    packpath = shallowutil.getcachepackpath(repo, PACK_CATEGORY)

    localpackpath = shallowutil.getlocalpackpath(repo.svfs.vfs.base,
                                                 PACK_CATEGORY)
    if repo.ui.configbool("treemanifest", "usecunionstore"):
        datastore = cstore.datapackstore(packpath)
        localdatastore = cstore.datapackstore(localpackpath)
        repo.svfs.manifestdatastore = cstore.uniondatapackstore(
                [localdatastore, datastore])
    else:
        datastore = datapackstore(repo.ui, packpath, usecdatapack=usecdatapack)
        localdatastore = datapackstore(repo.ui, localpackpath,
                                       usecdatapack=usecdatapack)

        repo.svfs.manifestdatastore = unioncontentstore(localdatastore,
            datastore, writestore=localdatastore)

    repo.svfs.sharedmanifestdatastores = [datastore]
    repo.svfs.localmanifestdatastores = [localdatastore]

    repo.svfs.sharedmanifesthistorystores = [
        historypackstore(repo.ui, packpath),
    ]
    repo.svfs.localmanifesthistorystores = [
        historypackstore(repo.ui, localpackpath),
    ]

class treemanifestlog(manifest.manifestlog):
    def __init__(self, opener):
        usetreemanifest = False
        cachesize = 4

        opts = getattr(opener, 'options', None)
        if opts is not None:
            usetreemanifest = opts.get('treemanifest', usetreemanifest)
            cachesize = opts.get('manifestcachesize', cachesize)
        self._treeinmem = usetreemanifest

        self._revlog = manifest.manifestrevlog(opener,
                                               indexfile='00manifesttree.i')

        # A cache of the manifestctx or treemanifestctx for each directory
        self._dirmancache = {}
        self._dirmancache[''] = util.lrucachedict(cachesize)

        self.cachesize = cachesize

def getmanifestlog(orig, self):
    mfl = orig(self)

    # The treemanifest needs a special opener with special options to enable
    # trees. The only way to get a copy of the opener with the exact same
    # configuration as the repo is to create it via a store, which requires the
    # repo object. So we need to build the opener here, then store it for later.
    pseudostore = store.store(self.requirements, self.path,
                              scmutil.vfs)
    opener = pseudostore.vfs
    opener.options = self.svfs.options.copy()
    opener.options.update({
        'treemanifest': True,
    })
    mfl.treemanifestlog = treemanifestlog(opener)

    return mfl

@command('backfilltree', [
    ('l', 'limit', '10000000', _(''))
    ], _('hg backfilltree [OPTIONS]'))
def backfilltree(ui, repo, *args, **opts):
    with repo.wlock():
        with repo.lock():
            with repo.transaction('backfilltree') as tr:
                _backfill(tr, repo, int(opts.get('limit')))

def _backfill(tr, repo, limit):
    ui = repo.ui
    cl = repo.changelog
    mfl = repo.manifestlog
    tmfl = mfl.treemanifestlog
    treerevlog = tmfl._revlog

    maxrev = len(treerevlog) - 1
    start = treerevlog.linkrev(maxrev) + 1
    end = min(len(cl), start + limit)

    converting = _("converting")

    ui.progress(converting, 0, total=end - start)
    for i in xrange(start, end):
        ctx = repo[i]
        newflat = ctx.manifest()
        p1 = ctx.p1()
        p2 = ctx.p2()
        p1node = p1.manifestnode()
        p2node = p2.manifestnode()
        if p1node != nullid:
            if (p1node not in treerevlog.nodemap or
                (p2node != nullid and p2node not in treerevlog.nodemap)):
                ui.warn(_("unable to find parent nodes %s %s\n") % (hex(p1node),
                                                                   hex(p2node)))
                return
            parentflat = mfl[p1node].read()
            parenttree = tmfl[p1node].read()
        else:
            parentflat = manifest.manifestdict()
            parenttree = manifest.treemanifest()

        diff = parentflat.diff(newflat)

        newtree = parenttree.copy()
        added = []
        removed = []
        for filename, (old, new) in diff.iteritems():
            if new is not None and new[0] is not None:
                added.append(filename)
                newtree[filename] = new[0]
                newtree.setflag(filename, new[1])
            else:
                removed.append(filename)
                del newtree[filename]

        try:
            oldaddrevision = treerevlog.addrevision
            def addusingnode(*args, **kwargs):
                newkwargs = kwargs.copy()
                newkwargs['node'] = ctx.manifestnode()
                return oldaddrevision(*args, **newkwargs)
            treerevlog.addrevision = addusingnode
            def readtree(dir, node):
                return tmfl.get(dir, node).read()
            treerevlog.add(newtree, tr, ctx.rev(), p1node, p2node, added,
                    removed, readtree=readtree)
        finally:
            del treerevlog.__dict__['addrevision']

        ui.progress(converting, i - start, total=end - start)

    ui.progress(converting, None)

def _unpackmanifests(orig, self, repo, *args, **kwargs):
    mfrevlog = repo.manifestlog._revlog
    oldtip = len(mfrevlog)

    orig(self, repo, *args, **kwargs)

    if (util.safehasattr(repo.svfs, "manifestdatastore") and
        repo.ui.configbool('treemanifest', 'autocreatetrees')):

        # TODO: only put in cache if pulling from main server
        packpath = shallowutil.getcachepackpath(repo, PACK_CATEGORY)
        with mutabledatapack(repo.ui, packpath) as dpack:
            with mutablehistorypack(repo.ui, packpath) as hpack:
                recordmanifest(dpack, hpack, repo, oldtip, len(mfrevlog))

        # Alert the store that there may be new packs
        repo.svfs.manifestdatastore.markforrefresh()

class InterceptedMutableDataPack(object):
    """This classes intercepts data pack writes and replaces the node for the
    root with the provided node. This is useful for forcing a tree manifest to
    be referencable via its flat hash.
    """
    def __init__(self, pack, node, p1node):
        self._pack = pack
        self._node = node
        self._p1node = p1node

    def add(self, name, node, deltabasenode, delta):
        # For the root node, provide the flat manifest as the key
        if name == "":
            node = self._node
            if deltabasenode != nullid:
                deltabasenode = self._p1node
        return self._pack.add(name, node, deltabasenode, delta)

class InterceptedMutableHistoryPack(object):
    """This classes intercepts history pack writes and does two things:
    1. replaces the node for the root with the provided node. This is
       useful for forcing a tree manifest to be referencable via its flat hash.
    2. Records the adds instead of sending them on. Since mutablehistorypack
       requires all entries for a file to be written contiguously, we need to
       record all the writes across the manifest import before sending them to
       the actual mutablehistorypack.
    """
    def __init__(self, node, p1node):
        self._node = node
        self._p1node = p1node
        self.entries = []

    def add(self, filename, node, p1, p2, linknode, copyfrom):
        # For the root node, provide the flat manifest as the key
        if filename == "":
            node = self._node
            if p1 != nullid:
                p1 = self._p1node
        self.entries.append((filename, node, p1, p2, linknode, copyfrom))

def recordmanifest(datapack, historypack, repo, oldtip, newtip):
    cl = repo.changelog
    mfl = repo.manifestlog
    mfrevlog = mfl._revlog
    total = newtip - oldtip
    ui = repo.ui
    builttrees = {}
    message = _('priming tree cache')
    ui.progress(message, 0, total=total)

    refcount = {}
    for rev in xrange(oldtip, newtip):
        p1 = mfrevlog.parentrevs(rev)[0]
        p1node = mfrevlog.node(p1)
        refcount[p1node] = refcount.get(p1node, 0) + 1

    allowedtreeroots = set()
    for name in repo.ui.configlist('treemanifest', 'allowedtreeroots'):
        if name in repo:
            allowedtreeroots.add(repo[name].manifestnode())

    includedentries = set()
    historyentries = {}
    for rev in xrange(oldtip, newtip):
        ui.progress(message, rev - oldtip, total=total)
        p1, p2 = mfrevlog.parentrevs(rev)
        p1node = mfrevlog.node(p1)
        p2node = mfrevlog.node(p2)
        linkrev = mfrevlog.linkrev(rev)
        linknode = cl.node(linkrev)

        if p1node == nullid:
            origtree = cstore.treemanifest(repo.svfs.manifestdatastore)
        elif p1node in builttrees:
            origtree = builttrees[p1node]
        else:
            origtree = mfl[p1node].read()._treemanifest()

        if origtree is None:
            if allowedtreeroots and p1node not in allowedtreeroots:
                continue

            p1mf = mfl[p1node].read()
            p1linknode = cl.node(mfrevlog.linkrev(p1))
            origtree = cstore.treemanifest(repo.svfs.manifestdatastore)
            for filename, node, flag in p1mf.iterentries():
                origtree.set(filename, node, flag)

            tempdatapack = InterceptedMutableDataPack(datapack, p1node, nullid)
            temphistorypack = InterceptedMutableHistoryPack(p1node, nullid)
            for nname, nnode, ntext, np1text, np1, np2 in origtree.finalize():
                # No need to compute a delta, since we know the parent isn't
                # already a tree.
                tempdatapack.add(nname, nnode, nullid, ntext)
                temphistorypack.add(nname, nnode, np1, np2, p1linknode, '')
                includedentries.add((nname, nnode))

            builttrees[p1node] = origtree

        # Remove the tree from the cache once we've processed its final use.
        # Otherwise memory explodes
        p1refcount = refcount[p1node] - 1
        if p1refcount == 0:
            builttrees.pop(p1node, None)
        refcount[p1node] = p1refcount

        if p2node != nullid:
            node = mfrevlog.node(rev)
            diff = mfl[p1node].read().diff(mfl[node].read())
            deletes = []
            adds = []
            for filename, ((anode, aflag), (bnode, bflag)) in diff.iteritems():
                if bnode is None:
                    deletes.append(filename)
                else:
                    adds.append((filename, bnode, bflag))
        else:
            # This will generally be very quick, since p1 == deltabase
            delta = mfrevlog.revdiff(p1, rev)

            deletes = []
            adds = []

            # Inspect the delta and read the added files from it
            current = 0
            end = len(delta)
            while current < end:
                try:
                    block = ''
                    # Deltas are of the form:
                    #   <start><end><datalen><data>
                    # Where start and end say what bytes to delete, and data
                    # says what bytes to insert in their place. So we can just
                    # read <data> to figure out all the added files.
                    byte1, byte2, blocklen = struct.unpack(">lll",
                            delta[current:current + 12])
                    current += 12
                    if blocklen:
                        block = delta[current:current + blocklen]
                        current += blocklen
                except struct.error:
                    raise RuntimeError("patch cannot be decoded")

                # An individual delta block may contain multiple newline
                # delimited entries.
                for line in block.split('\n'):
                    if not line:
                        continue
                    fname, rest = line.split('\0')
                    fnode = rest[:40]
                    fflag = rest[40:]
                    adds.append((fname, bin(fnode), fflag))

            allfiles = set(repo.changelog.readfiles(linkrev))
            deletes = allfiles.difference(fname for fname, fnode, fflag in adds)

        # Apply the changes on top of the parent tree
        newtree = origtree.copy()
        for fname in deletes:
            newtree.set(fname, None, None)

        for fname, fnode, fflags in adds:
            newtree.set(fname, fnode, fflags)

        tempdatapack = InterceptedMutableDataPack(datapack, mfrevlog.node(rev),
                                                  p1node)
        temphistorypack = InterceptedMutableHistoryPack(mfrevlog.node(rev),
                                                        p1node)
        newtreeiter = newtree.finalize(origtree if p1node != nullid else None)
        for nname, nnode, ntext, np1text, np1, np2 in newtreeiter:
            # Only use deltas if the delta base is in this same pack file
            if np1 != nullid and (nname, np1) in includedentries:
                delta = mdiff.textdiff(np1text, ntext)
                deltabase = np1
            else:
                delta = ntext
                deltabase = nullid
            tempdatapack.add(nname, nnode, deltabase, delta)
            temphistorypack.add(nname, nnode, np1, np2, linknode, '')
            includedentries.add((nname, nnode))

        for entry in temphistorypack.entries:
            filename, values = entry[0], entry[1:]
            historyentries.setdefault(filename, []).append(values)

        if ui.configbool('treemanifest', 'verifyautocreate', False):
            diff = newtree.diff(origtree)
            if len(diff) != len(adds) + len(deletes):
                import pdb
                pdb.set_trace()

            for fname in deletes:
                fdiff = diff.get(fname)
                if fdiff is None:
                    import pdb
                    pdb.set_trace()
                    pass
                else:
                    l, r = fdiff
                    if l != (None, ''):
                        import pdb
                        pdb.set_trace()
                        pass

            for fname, fnode, fflags in adds:
                fdiff = diff.get(fname)
                if fdiff is None:
                    # Sometimes adds are no-ops, so they don't show up in the
                    # diff.
                    if origtree.get(fname) != newtree.get(fname):
                        import pdb
                        pdb.set_trace()
                        pass
                else:
                    l, r = fdiff
                    if l != (fnode, fflags):
                        import pdb
                        pdb.set_trace()
                        pass
        builttrees[mfrevlog.node(rev)] = newtree

        mfnode = mfrevlog.node(rev)
        if refcount.get(mfnode) > 0:
            builttrees[mfnode] = newtree

    ui.progress(message, None)

    for filename, entries in sorted(historyentries.iteritems()):
        for entry in entries:
            historypack.add(filename, *entry)

def _checkhash(orig, self, *args, **kwargs):
    # Don't validate root hashes during the transition to treemanifest
    if self.indexfile.endswith('00manifesttree.i'):
        return
    return orig(self, *args, **kwargs)

def wrappropertycache(cls, propname, wrapper):
    """Wraps a filecache property. These can't be wrapped using the normal
    wrapfunction. This should eventually go into upstream Mercurial.
    """
    assert callable(wrapper)
    for currcls in cls.__mro__:
        if propname in currcls.__dict__:
            origfn = currcls.__dict__[propname].func
            assert callable(origfn)
            def wrap(*args, **kwargs):
                return wrapper(origfn, *args, **kwargs)
            currcls.__dict__[propname].func = wrap
            break

    if currcls is object:
        raise AttributeError(_("%s has no property '%s'") %
                             (type(currcls), propname))
