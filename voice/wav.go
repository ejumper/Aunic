package voice

import "encoding/binary"

// WrapPCM prepends a standard 44-byte WAV header to raw PCM bytes
// (24kHz, mono, signed 16-bit little-endian) and returns the complete WAV file.
func WrapPCM(pcm []byte) []byte {
	dataSize := uint32(len(pcm))
	riffSize := dataSize + 36

	hdr := make([]byte, 44)
	copy(hdr[0:4], "RIFF")
	binary.LittleEndian.PutUint32(hdr[4:8], riffSize)
	copy(hdr[8:12], "WAVE")
	copy(hdr[12:16], "fmt ")
	binary.LittleEndian.PutUint32(hdr[16:20], 16)     // PCM chunk size
	binary.LittleEndian.PutUint16(hdr[20:22], 1)      // PCM format
	binary.LittleEndian.PutUint16(hdr[22:24], 1)      // mono
	binary.LittleEndian.PutUint32(hdr[24:28], 24000)  // sample rate
	binary.LittleEndian.PutUint32(hdr[28:32], 48000)  // byte rate (24000 * 2 bytes/sample)
	binary.LittleEndian.PutUint16(hdr[32:34], 2)      // block align
	binary.LittleEndian.PutUint16(hdr[34:36], 16)     // bits per sample
	copy(hdr[36:40], "data")
	binary.LittleEndian.PutUint32(hdr[40:44], dataSize)

	return append(hdr, pcm...)
}
