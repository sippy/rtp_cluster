#!/bin/sh

set -e

BASEDIR="${BASEDIR:-$(dirname -- "${0}")/..}"
BASEDIR="$(readlink -f -- ${BASEDIR})"

#. ${BASEDIR}/functions

if [ -e "${BUILDDIR}/dist" ]
then
  rm -rf "${BUILDDIR}/dist"
fi
mkdir "${BUILDDIR}/dist"
cd "${BUILDDIR}/dist"

RTPP_BRANCH="master"
MAKE_CMD="make"
git clone -b "${RTPP_BRANCH}" --recursive git://github.com/sippy/rtpproxy.git
cd rtpproxy
./configure
${MAKE_CMD} all
