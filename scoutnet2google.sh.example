#!/bin/sh

ROOT=`dirname $0`

if [ "$ROOT" = "." ]; then
	ROOT=`pwd`
fi

docker run --rm -v $ROOT/conf:/scout/conf $@ scoutnet2google
