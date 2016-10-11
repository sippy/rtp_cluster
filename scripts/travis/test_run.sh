#!/bin/sh

set -e

BASEDIR="${BASEDIR:-$(dirname -- "${0}")/../..}"
BASEDIR="$(readlink -f -- ${BASEDIR})"

. ${BASEDIR}/scripts/travis/functions.sub

cd ${TESTSDIR}
init_mr_time
RTPPROXY_CMD="${RTPPROXY_BIN}" exec ${SHELL} basic_network
