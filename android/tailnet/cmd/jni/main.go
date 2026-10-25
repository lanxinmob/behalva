package main

/*
#include <stdlib.h>
*/
import "C"

import (
	bridge "personalassistant.local/tailnet"
	"unsafe"
)

//export pa_tailnet_call
func pa_tailnet_call(data *C.char, length C.int, outputLength *C.int) unsafe.Pointer {
	answer := bridge.Call(C.GoBytes(unsafe.Pointer(data), length))
	*outputLength = C.int(len(answer))
	return C.CBytes(answer)
}

func main() {}
