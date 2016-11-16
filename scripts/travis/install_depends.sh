#!/bin/sh

set -e

BASEDIR="${BASEDIR:-$(dirname -- "${0}")/../..}"
BASEDIR="$(readlink -f -- ${BASEDIR})"

. ${BASEDIR}/scripts/travis/functions.sub

if [ -e "${DISTDIR}" ]
then
  rm -rf "${DISTDIR}"
fi
mkdir "${DISTDIR}"
cd "${DISTDIR}"

RTPP_BRANCH=${RTPP_BRANCH:-"master"}
MAKE_CMD="make"
git clone -b "${RTPP_BRANCH}" --recursive git://github.com/sippy/rtpproxy.git

##if [ "${RTPP_BRANCH}" != "master" ]
##then
##  git clone -b master --recursive git://github.com/sippy/rtpproxy.git \
##   "${RTPPDDIR_m}"
##fi

cd rtpproxy
./configure
${MAKE_CMD} all
