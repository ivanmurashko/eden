scratchnodes() {
  for node in `find ../repo/.hg/scratchbranches/index/nodemap/* | sort`; do
     echo ${node##*/} `cat $node`
  done
}

scratchbookmarks() {
  for bookmark in `find ../repo/.hg/scratchbranches/index/bookmarkmap/* -type f | sort`; do
     echo "${bookmark##*/bookmarkmap/} `cat $bookmark`"
  done
}

setupcommon() {
  extpath=`dirname $TESTDIR`
  cp -r $extpath/infinitepush $TESTTMP
  cp -r $extpath/infinitepush $TESTTMP

  cat >> $HGRCPATH << EOF
[extensions]
infinitepush=$TESTTMP/infinitepush
[ui]
ssh = python "$TESTDIR/dummyssh"
[infinitepush]
branchpattern=re:scratch/.*
EOF
}

setupserver() {
cat >> .hg/hgrc << EOF
[infinitepush]
server=yes
indextype=disk
storetype=disk
EOF
}

setupsqlclienthgrc() {
cat << EOF > .hg/hgrc
[ui]
ssh=python "$TESTDIR/dummyssh"
[extensions]
infinitepush=$TESTTMP/infinitepush
[infinitepush]
branchpattern=re:scratch/.+
server=False
[paths]
default = ssh://user@dummy/server
EOF
}

setupsqlserverhgrc() {
cat << EOF > .hg/hgrc
[ui]
ssh=python "$TESTDIR/dummyssh"
[extensions]
infinitepush=$TESTTMP/infinitepush
[infinitepush]
branchpattern=re:scratch/.+
server=True
indextype=sql
storetype=disk
EOF
}

createdb() {
mysql -h $DBHOST -P $DBPORT -u $DBUSER -p"$DBPASS" -e "CREATE DATABASE IF NOT EXISTS $DBNAME;" 2>/dev/null
mysql -h $DBHOST -P $DBPORT -D $DBNAME -u $DBUSER -p"$DBPASS" -e '
DROP TABLE IF EXISTS nodestobundle;
DROP TABLE IF EXISTS bookmarkstonode;
DROP TABLE IF EXISTS bundles;
CREATE TABLE IF NOT EXISTS nodestobundle(
node CHAR(40) BINARY NOT NULL,
bundle VARCHAR(512) BINARY NOT NULL,
reponame CHAR(255) BINARY NOT NULL,
PRIMARY KEY(node, reponame));

CREATE TABLE IF NOT EXISTS bookmarkstonode(
node CHAR(40) BINARY NOT NULL,
bookmark VARCHAR(512) BINARY NOT NULL,
reponame CHAR(255) BINARY NOT NULL,
PRIMARY KEY(reponame, bookmark));

CREATE TABLE IF NOT EXISTS bundles(
bundle VARCHAR(512) BINARY NOT NULL,
reponame CHAR(255) BINARY NOT NULL,
PRIMARY KEY(bundle, reponame));' 2>/dev/null
}

setupdb() {
DBHOSTPORT=`$TESTDIR/getdb.sh` || exit 1
echo "sqlhost=$DBHOSTPORT" >> .hg/hgrc
echo "reponame=babar" >> .hg/hgrc
DBHOST=`echo $DBHOSTPORT | cut -d : -f 1`
DBPORT=`echo $DBHOSTPORT | cut -d : -f 2`
DBNAME=`echo $DBHOSTPORT | cut -d : -f 3`
DBUSER=`echo $DBHOSTPORT | cut -d : -f 4`
DBPASS=`echo $DBHOSTPORT | cut -d : -f 5-`
createdb
}
