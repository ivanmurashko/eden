# Copyright (c) Facebook, Inc. and its affiliates.
#
# This software may be used and distributed according to the terms of the
# GNU General Public License found in the LICENSE file in the root
# directory of this source tree.

  $ . "${TEST_FIXTURES}/library.sh"

Setup config repo:
  $ POPULATE_GIT_MAPPING=1 setup_common_config
  $ setup_configerator_configs
  $ cd "$TESTTMP"

Setup testing repo for mononoke:
  $ hg init repo-hg
  $ cd repo-hg
  $ setup_hg_server

Helper for making commit:
  $ function commit() { # the arg is used both for commit message and variable name
  >   hg commit -Am $1 # create commit
  >   export COMMIT_$1="$(hg --debug id -i)" # save hash to variable
  > }

First two simple commits and bookmark:
  $ echo -e "a\nb\nc\nd\ne" > a
  $ commit A
  adding a

  $ echo -e "a\nb\nd\ne\nf" > b
  $ commit B
  adding b
  $ hg bookmark -i BOOKMARK_B

A commit with a file change and binary file

  $ echo -e "b\nc\nd\ne\nf" > b
  $ echo -e "\0 10" > binary
  $ commit C
  adding binary

Commit with globalrev:
  $ touch c
  $ hg add
  adding c
  $ hg commit -Am "commit with globalrev" --extra global_rev=9999999999
  $ hg bookmark -i BOOKMARK_C

Commit git SHA:
  $ touch d
  $ hg add
  adding d
  $ hg commit -Am "commit with git sha" --extra convert_revision=37b0a167e07f2b84149c918cec818ffeb183dddd --extra hg-git-rename-source=git

import testing repo to mononoke
  $ cd ..
  $ blobimport repo-hg/.hg repo --has-globalrev

try talking to the server before it is up
  $ SCS_PORT=$(get_free_socket) scsc lookup --repo repo  -B BOOKMARK_B
  error: apache::thrift::transport::TTransportException: AsyncSocketException: connect failed, type = Socket not open, errno = 111 (Connection refused): Connection refused
  [1]

start SCS server
  $ start_and_wait_for_scs_server --scuba-log-file "$TESTTMP/scuba.json"

ignore the scuba logs logged while starting the server
  $ SCUBA_PREAMBLE=$(( $(wc -l < "$TESTTMP/scuba.json") + 1 ))

make some simple requests that we can use to check scuba logging

repos
  $ scsc repos
  repo

lookup using bookmark
  $ scsc lookup --repo repo -B BOOKMARK_C -S bonsai
  006c988c4a9f60080a6bc2a2fff47565fafea2ca5b16c4d994aecdef0c89973b

diff paths only
  $ scsc diff --repo repo --paths-only -B BOOKMARK_B --bonsai-id "006c988c4a9f60080a6bc2a2fff47565fafea2ca5b16c4d994aecdef0c89973b"
  M b
  A binary
  A c

check the scuba methods and perf counters logs 
  $ tail -n +$SCUBA_PREAMBLE "$TESTTMP/scuba.json" | summarize_scuba_json "Request.*" \
  >     .normal.log_tag .normal.msg .normal.method \
  >     .normal.commit .normal.other_commit .normal.path \
  >     .normal.bookmark_name .normvector.identity_schemes \
  >     .normal.status .normal.error \
  >     .int.BlobGets
  {
    "log_tag": "Request start",
    "method": "list_repos"
  }
  {
    "BlobGets": 0,
    "log_tag": "Request complete",
    "method": "list_repos",
    "status": "SUCCESS"
  }
  {
    "bookmark_name": "BOOKMARK_C",
    "identity_schemes": [
      "BONSAI"
    ],
    "log_tag": "Request start",
    "method": "repo_resolve_bookmark"
  }
  {
    "BlobGets": 0,
    "bookmark_name": "BOOKMARK_C",
    "identity_schemes": [
      "BONSAI"
    ],
    "log_tag": "Request complete",
    "method": "repo_resolve_bookmark",
    "status": "SUCCESS"
  }
  {
    "commit": "006c988c4a9f60080a6bc2a2fff47565fafea2ca5b16c4d994aecdef0c89973b",
    "identity_schemes": [
      "BONSAI"
    ],
    "log_tag": "Request start",
    "method": "commit_lookup"
  }
  {
    "BlobGets": 0,
    "commit": "006c988c4a9f60080a6bc2a2fff47565fafea2ca5b16c4d994aecdef0c89973b",
    "identity_schemes": [
      "BONSAI"
    ],
    "log_tag": "Request complete",
    "method": "commit_lookup",
    "status": "SUCCESS"
  }
  {
    "bookmark_name": "BOOKMARK_B",
    "identity_schemes": [
      "BONSAI"
    ],
    "log_tag": "Request start",
    "method": "repo_resolve_bookmark"
  }
  {
    "BlobGets": 0,
    "bookmark_name": "BOOKMARK_B",
    "identity_schemes": [
      "BONSAI"
    ],
    "log_tag": "Request complete",
    "method": "repo_resolve_bookmark",
    "status": "SUCCESS"
  }
  {
    "commit": "006c988c4a9f60080a6bc2a2fff47565fafea2ca5b16c4d994aecdef0c89973b",
    "identity_schemes": [
      "BONSAI"
    ],
    "log_tag": "Request start",
    "method": "commit_compare",
    "other_commit": "c63b71178d240f05632379cf7345e139fe5d4eb1deca50b3e23c26115493bbbb"
  }
  {
    "BlobGets": 0,
    "commit": "006c988c4a9f60080a6bc2a2fff47565fafea2ca5b16c4d994aecdef0c89973b",
    "identity_schemes": [
      "BONSAI"
    ],
    "log_tag": "Request complete",
    "method": "commit_compare",
    "other_commit": "c63b71178d240f05632379cf7345e139fe5d4eb1deca50b3e23c26115493bbbb",
    "status": "SUCCESS"
  }

commands after this point may run requests in parallel, which can change the ordering
of the scuba samples.

cat a file
  $ scsc cat --repo repo -B BOOKMARK_B -p a
  a
  b
  c
  d
  e

show commit info
  $ scsc info --repo repo -i ee87eb8cfeb218e7352a94689b241ea973b80402
  Commit: ee87eb8cfeb218e7352a94689b241ea973b80402
  Parent: c29e0e474e30ae40ed639fa6292797a7502bc590
  Date: 1970-01-01 00:00:00 +00:00
  Author: test
  Generation: 4
  Extra:
      global_rev=9999999999
  
  commit with globalrev

  $ scsc info --repo repo -i ee87eb8cfeb218e7352a94689b241ea973b80402 -S bonsai,hg,globalrev
  Commit:
      bonsai=006c988c4a9f60080a6bc2a2fff47565fafea2ca5b16c4d994aecdef0c89973b
      globalrev=9999999999
      hg=ee87eb8cfeb218e7352a94689b241ea973b80402
  Parent:
      bonsai=d5ded5e738f4fc36b03c3e09db9cdd9259d167352a03fb6130f5ee138b52972f
      hg=c29e0e474e30ae40ed639fa6292797a7502bc590
  Date: 1970-01-01 00:00:00 +00:00
  Author: test
  Generation: 4
  Extra:
      global_rev=9999999999
  
  commit with globalrev

show commit info for git commit
  $ scsc info --repo repo -i 37b0a167e07f2b84149c918cec818ffeb183dddd -S bonsai,hg,globalrev,git
  Commit:
      bonsai=227d4402516061c45a7ba66cf4561bdadaf3ac96eb12c6e75aa9c72dbabd42b6
      git=37b0a167e07f2b84149c918cec818ffeb183dddd
      hg=6e602c2eaa591b482602f5f3389de6c2749516d5
  Parent:
      bonsai=006c988c4a9f60080a6bc2a2fff47565fafea2ca5b16c4d994aecdef0c89973b
      globalrev=9999999999
      hg=ee87eb8cfeb218e7352a94689b241ea973b80402
  Date: 1970-01-01 00:00:00 +00:00
  Author: test
  Generation: 5
  Extra:
      convert_revision=37b0a167e07f2b84149c918cec818ffeb183dddd
      hg-git-rename-source=git
  
  commit with git sha

show tree info
  $ scsc info --repo repo -i ee87eb8cfeb218e7352a94689b241ea973b80402 -p ""
  Path: 
  Type: tree
  Id: 7403a559399d2aeb6b0e58f62131ac121a3347ec6342201895d34036d87c726e
  Simple-Format-SHA1: 7c6d1b3745da28107356823689cb2b83c4132f7c
  Simple-Format-SHA256: 57abececda70ab40c538a02743987a7e5f829581986c582fc11e7fe9d37b7bac
  Children: 4 files (25 bytes), 0 dirs
  Descendants: 4 files (25 bytes)

show file info
  $ scsc info --repo repo -i ee87eb8cfeb218e7352a94689b241ea973b80402 -p a
  Path: a
  Type: file
  Id: af1950dbdacd7eee24e4dbb7de9bcbf1f6b05c4a24b066deab407e9143715702
  Content-SHA1: 6249443f65b64a5ac07802a3582fd5c1f5f2ebd8
  Content-SHA256: 86dc03602dcf385217216784784a8ecf20e6400decc3208170b12fcb0afb6698
  Size: 10 bytes

show file info with multiple paths
  $ scsc info --repo repo -i ee87eb8cfeb218e7352a94689b241ea973b80402 -p a ""
  Path: 
  Type: tree
  Id: 7403a559399d2aeb6b0e58f62131ac121a3347ec6342201895d34036d87c726e
  Simple-Format-SHA1: 7c6d1b3745da28107356823689cb2b83c4132f7c
  Simple-Format-SHA256: 57abececda70ab40c538a02743987a7e5f829581986c582fc11e7fe9d37b7bac
  Children: 4 files (25 bytes), 0 dirs
  Descendants: 4 files (25 bytes)
  Path: a
  Type: file
  Id: af1950dbdacd7eee24e4dbb7de9bcbf1f6b05c4a24b066deab407e9143715702
  Content-SHA1: 6249443f65b64a5ac07802a3582fd5c1f5f2ebd8
  Content-SHA256: 86dc03602dcf385217216784784a8ecf20e6400decc3208170b12fcb0afb6698
  Size: 10 bytes

list directory
  $ scsc ls --repo repo -i ee87eb8cfeb218e7352a94689b241ea973b80402
  a
  b
  binary
  c

  $ scsc ls --repo repo -i ee87eb8cfeb218e7352a94689b241ea973b80402 -l
  file        10  a
  file        10  b
  file         5  binary
  file         0  c

export
  $ scsc export --repo repo -i ee87eb8cfeb218e7352a94689b241ea973b80402 -p "" -o exported
  $ find exported | sort
  exported
  exported/a
  exported/b
  exported/binary
  exported/c
  $ cat exported/a
  a
  b
  c
  d
  e
