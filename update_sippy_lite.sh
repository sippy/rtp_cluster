#!/bin/sh

set -e

git checkout sippy_git_master
git pull
git branch -D sippy_git_master_toplevel
git subtree split --prefix=sippy --branch=sippy_git_master_toplevel
git checkout sippy_git_master_toplevel
mkdir -p sippy_lite/Math
mkdir -p sippy_lite/Time
for f in `cat sippy_lite.list`
do
  git mv ${f} sippy_lite/${f}
done
git rm .gitignore dictionary *.py
false
git checkout master
git pull
git merge sippy_git_master_toplevel
git commit
git push
