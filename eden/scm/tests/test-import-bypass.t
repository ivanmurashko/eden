  $ enable amend
  $ hg up 'desc(adda)'
  $ hg hide -q tip
  @  3:d805bc8236b6 test 0 0 - addabcd
  o  4:5bd46886ca3e test 0 0 - changeabcd
  @  3:d805bc8236b6 test 0 0 - addabcd
  $ hg diff --change 'desc(changeabcd)' --git