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

RTPP_REV=${RTPP_BRANCH:-"master"}
MAKE_CMD="make"
git clone --recursive git://github.com/sippy/rtpproxy.git

if [ "${RTPP_REV}" != "master" ]
then
  git -C rtpproxy checkout "${RTPP_REV}"
##  git clone -b master --recursive git://github.com/sippy/rtpproxy.git \
##   "${RTPPDDIR_m}"
fi

cd rtpproxy
./configure
${MAKE_CMD} all
cd ..

git clone git://github.com/sobomax/libelperiodic.git
cd libelperiodic
./configure
make all
sudo make install
sudo ldconfig
python setup.py clean build install
python -c "from elperiodic.ElPeriodic import ElPeriodic"
cd ..
