//go:build !windows

package main

import "embed"

//go:embed resources/common/*.gz resources/linux/*.gz
var coreFS embed.FS

const coreSubdir = "resources/linux"
