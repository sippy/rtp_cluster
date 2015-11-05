#!/bin/sh

set -e

git checkout sippy_git_master
git pull
git branch -d sippy_git_master_toplevel
git subtree split --prefix=sippy --branch=sippy_git_master_toplevel
git checkout master
git pull
git merge sippy_git_master_toplevel
git commit
git push
