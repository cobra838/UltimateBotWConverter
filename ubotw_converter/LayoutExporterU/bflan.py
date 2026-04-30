import struct
from common import readString, roundUp, Section


class FLAN:
    class AnimationTagBlock(Section):
        class AnimationGroupRef:
            def __init__(self, file, pos, fmt):
                (nameBytes,
                 self.flag) = struct.unpack_from(fmt, file, pos)

                self.name = readString(nameBytes)
                self.fmt = fmt

            def save(self, endian='>'):
                return struct.pack(
                    self.fmt,
                    self.name.encode('utf-8'),
                    self.flag,
                )

        def __init__(self, file, pos, major, endian):
            initPos = pos
            super().__init__(file, pos, endian); pos += 8
            if endian == '<':
                (self.tagOrder,
                 self.groupNum,
                 self.nameOffset,
                 self.groupsOffset,
                 _reserved,
                 self.startFrame,
                 self.endFrame) = struct.unpack_from(f'{endian}2H2IIhh', file, pos)
                self.flag = 0
            else:
                (self.tagOrder,
                 self.groupNum,
                 self.nameOffset,
                 self.groupsOffset,
                 self.startFrame,
                 self.endFrame,
                 self.flag) = struct.unpack_from(f'{endian}2H2I2hB', file, pos)

            # flag & 1 -> ANIMTAGFLAG_DESCENDINGBIND

            pos = initPos + self.nameOffset
            self.name = readString(file, pos)

            pos = initPos + self.groupsOffset
            self.groups = []

            if major < 5:
                fmt = f'{endian}24sB3x'
                size = 28

            else:
                fmt = f'{endian}33sB2x'
                size = 36

            for _ in range(self.groupNum):
                self.groups.append(self.AnimationGroupRef(file, pos, fmt))
                pos += size

        def save(self, endian='>'):
            extra = b'\0' * 4 if endian == '<' else b''
            nameOffset = 32 if endian == '<' else 28
            buff1 = b''.join([extra, self.name.encode('utf-8'), b'\0'])

            groupsOffset = nameOffset + len(buff1) - len(extra)
            alignLen = roundUp(groupsOffset, 4) - groupsOffset

            groupsOffset += alignLen
            buff1 += b'\0' * alignLen

            if endian == '<':
                buff2 = struct.pack(
                    f'{endian}2H2IIhh',
                    self.tagOrder,
                    len(self.groups),
                    nameOffset,
                    groupsOffset,
                    0,
                    self.startFrame,
                    self.endFrame,
                )
            else:
                buff2 = struct.pack(
                    f'{endian}2H2I2hB3x',
                    self.tagOrder,
                    len(self.groups),
                    nameOffset,
                    groupsOffset,
                    self.startFrame,
                    self.endFrame,
                    self.flag,
                )

            buff3 = b''.join([group.save(endian) for group in self.groups])
            self.data = b''.join([buff2, buff1, buff3])

            return super().save(endian)

    class AnimationShareBlock(Section):
        class AnimationShareInfo:
            def __init__(self, file, pos, endian):
                (srcPaneNameBytes,
                 targetGroupNameBytes) = struct.unpack_from(f'{endian}25s25s', file, pos)

                self.srcPaneName = readString(srcPaneNameBytes)
                self.targetGroupName = readString(targetGroupNameBytes)

                #print(self.srcPaneName)

            def save(self, endian='>'):
                return struct.pack(
                    f'{endian}25s25s2x',
                    self.srcPaneName.encode('utf-8'),
                    self.targetGroupName.encode('utf-8'),
                )

        def __init__(self, file, pos, endian):
            super().__init__(file, pos, endian); pos += 8

            (self.animShareInfoOffset,
             self.shareNum) = struct.unpack_from(f'{endian}IH', file, pos)

            pos += self.animShareInfoOffset - 8
            self.animShareInfos = []

            for _ in range(self.shareNum):
                self.animShareInfos.append(self.AnimationShareInfo(file, pos, endian))
                pos += 52

        def save(self, endian='>'):
            buff1 = struct.pack(
                f'{endian}IH2x',
                16,
                len(self.animShareInfos),
            )

            buff2 = b''.join([animShareInfo.save(endian) for animShareInfo in self.animShareInfos])
            self.data = b''.join([buff1, buff2])

            return super().save(endian)

    class AnimationBlock(Section):
        class AnimationContent:
            class AnimationInfo:
                class AnimationTarget:
                    class HermiteKey:
                        def __init__(self, file, pos, endian):
                            (self.frame,
                             self.value,
                             self.slope) = struct.unpack_from(f'{endian}3f', file, pos)

                        def save(self, endian='>'):
                            return struct.pack(
                                f'{endian}3f',
                                self.frame,
                                self.value,
                                self.slope,
                            )

                    class StepKey:
                        def __init__(self, file, pos, endian):
                            (self.frame,
                             self.value) = struct.unpack_from(f'{endian}fH2x', file, pos)

                        def save(self, endian='>'):
                            return struct.pack(
                                f'{endian}fH2x',
                                self.frame,
                                self.value,
                            )

                    def __init__(self, file, pos, endian):
                        initPos = pos
                        _curveTypes = ["Constant", "Step", "Hermite"]

                        (self.id,
                         self.target,
                         self.curveType,
                         self.keyNum,
                         self.keysOffset) = struct.unpack_from(f'{endian}3BxH2xI', file, pos); pos += 12

                        assert self.keysOffset == 12

                        self.keys = []
                        if self.curveType:
                            size = 8 if self.curveType == 1 else 12
                            key = self.StepKey if self.curveType == 1 else self.HermiteKey
                            pos = initPos + self.keysOffset

                            for _ in range(self.keyNum):
                                self.keys.append(key(file, pos, endian)); pos += size

                    def save(self, endian='>'):
                        buff1 = struct.pack(
                            f'{endian}3BxH2xI',
                            self.id,
                            self.target,
                            self.curveType,
                            len(self.keys),
                            12,
                        )

                        buff2 = bytearray()
                        for key in self.keys:
                            buff2 += key.save(endian)

                        return b''.join([buff1, buff2])

                def __init__(self, file, pos, endian):
                    initPos = pos

                    (self.magic,
                     self.num) = struct.unpack_from(f'{endian}4sB', file, pos); pos += 8

                    self.animTargets = []
                    animTargetOffsets = struct.unpack_from(f'{endian}{self.num}I', file, pos)

                    for pAnimTarget in animTargetOffsets:
                        self.animTargets.append(self.AnimationTarget(file, initPos + pAnimTarget, endian))

                def save(self, endian='>'):
                    num = len(self.animTargets)

                    buff1 = struct.pack(
                        f'{endian}4sB3x',
                        self.magic,
                        num,
                    )

                    buff2 = bytearray()

                    animTargetOffsets = []
                    for animTarget in self.animTargets:
                        animTargetOffsets.append(8 + 4*num + len(buff2))
                        buff2 += animTarget.save(endian)

                    buff3 = struct.pack(f'{endian}{num}I', *animTargetOffsets)

                    return b''.join([buff1, buff3, buff2])

            def __init__(self, file, pos, endian):
                initPos = pos

                _types = ["Pane", "Material"]

                (nameBytes,
                 self.num,
                 self.type) = struct.unpack_from(f'{endian}28s2B', file, pos); pos += 32

                self.name = readString(nameBytes)
                #print(self.name)

                animInfoOffsets = struct.unpack_from(f'{endian}{self.num}I', file, pos)
                self.animInfos = []

                for pAnimInfo in animInfoOffsets:
                    self.animInfos.append(self.AnimationInfo(file, initPos + pAnimInfo, endian))

            def save(self, endian='>'):
                num = len(self.animInfos)

                buff1 = struct.pack(
                    f'{endian}28s2B2x',
                    self.name.encode('utf-8'),
                    num,
                    self.type,
                )

                buff2 = bytearray()

                animInfoOffsets = []
                for animInfo in self.animInfos:
                    animInfoOffsets.append(32 + 4*num + len(buff2))
                    buff2 += animInfo.save(endian)

                buff3 = struct.pack(f'{endian}{num}I', *animInfoOffsets)

                return b''.join([buff1, buff3, buff2])

        def __init__(self, file, pos, endian):
            initPos = pos
            super().__init__(file, pos, endian); pos += 8

            (self.frameSize,
             self.loop,
             self.fileNum,
             self.animContNum,
             animContOffsetsOffset) = struct.unpack_from(f'{endian}HBx2HI', file, pos); pos += 12

            self.fileNames = []
            self.formats = []

            for i in range(self.fileNum):
                pFileName, = struct.unpack_from(f'{endian}I', file, pos + 4*i)
                fileName = readString(file, pos + pFileName)
                format = ""

                if fileName.endswith(".bflim"):
                    fileName = fileName[:-6]

                if len(fileName) > 2:
                    if fileName[-1] in 'abcdefghijklmnopqrstu' and fileName[-2] in '^+':
                        format = fileName[-2:]
                        fileName = fileName[:-2]

                self.fileNames.append(fileName)
                self.formats.append(format)

            pos = initPos + animContOffsetsOffset
            animContOffsets = struct.unpack_from(f'{endian}{self.animContNum}I', file, pos)
            self.animConts = []

            for pAnimCont in animContOffsets:
                self.animConts.append(self.AnimationContent(file, initPos + pAnimCont, endian))

        def save(self, endian='>'):
            fileNum = len(self.fileNames)
            animContNum = len(self.animConts)

            buff1 = bytearray()

            fileNameOffsets = []
            for fileName, format in zip(self.fileNames, self.formats):
                fileNameOffsets.append(4*fileNum + len(buff1))

                cFileName = ''.join([fileName, format, ".bflim"])
                buff1 += cFileName.encode('utf-8')
                buff1.append(0)

            buff2 = struct.pack(f'{endian}{fileNum}I', *fileNameOffsets)

            animContOffsetsOffset = 20 + 4*fileNum + len(buff1)
            alignLen = roundUp(animContOffsetsOffset, 4) - animContOffsetsOffset

            animContOffsetsOffset += alignLen
            buff1 += b'\0' * alignLen

            buff3 = struct.pack(
                f'{endian}HBx2HI',
                self.frameSize,
                self.loop,
                fileNum,
                animContNum,
                animContOffsetsOffset,
            )

            buff4 = bytearray()

            animContOffsets = []
            for animCont in self.animConts:
                animContOffsets.append(animContOffsetsOffset + 4*animContNum + len(buff4))
                buff4 += animCont.save(endian)

            buff5 = struct.pack(f'{endian}{animContNum}I', *animContOffsets)
            self.data = b''.join([buff3, buff2, buff1, buff5, buff4])

            return super().save(endian)

    def __init__(self, file):
        if file[4:6] == b'\xFE\xFF':
            endian = '>'
        elif file[4:6] == b'\xFF\xFE':
            endian = '<'
        else:
            raise NotImplementedError("Unsupported BFLAN byte order")
        self.endian = endian

        (self.magic,
         self.headSize,
         self.version,
         self.fileSize,
         self.numSections) = struct.unpack_from(f'{endian}4s2xH2IH', file)

        assert self.magic == b'FLAN'
        major = self.version >> 24
        if major not in [2, 3, 5]:
            print("Untested BFLAN version: %s\n" % hex(self.version))

        self.tag = None
        self.share = None
        self.info = None

        pos = 20
        for _ in range(self.numSections):
            if file[pos:pos + 4] == b'pat1':
                self.tag = self.AnimationTagBlock(file, pos, major, endian)
                pos += self.tag.blockHeader.size

            elif file[pos:pos + 4] == b'pah1':
                self.share = self.AnimationShareBlock(file, pos, endian)
                pos += self.share.blockHeader.size

            elif file[pos:pos + 4] == b'pai1':
                self.info = self.AnimationBlock(file, pos, endian)
                pos += self.info.blockHeader.size

    def save(self, endian='>'):
        buff1 = bytearray()

        numSections = 0
        if self.tag:
            buff1 += self.tag.save(endian); numSections += 1

        if self.share:
            buff1 += self.share.save(endian); numSections += 1

        if self.info:
            buff1 += self.info.save(endian); numSections += 1

        bom = 0xFEFF

        buff2 = struct.pack(
            f'{endian}4s2H2IH2x',
            b'FLAN',
            bom,
            20,
            self.version,
            20 + len(buff1),
            numSections,
        )

        return b''.join([buff2, buff1])


def toVersion(file, output, version):
    major = version >> 24  # TODO little endian
    endian = '<' if major >= 8 else '>'
    fmt = f'{endian}24sB3x' if major < 5 else f'{endian}33sB2x'

    flan = FLAN(file)
    flan.version = version

    if flan.tag:
        for group in flan.tag.groups:
            group.fmt = fmt

    with open(output, "wb") as out:
        out.write(flan.save(endian))


def main():
    file = input("Input (.bflan):  ")
    output = input("Output (.bflan):  ")
    version = int(input("Convert to version (e.g. 0x02020000):  "), 0)

    with open(file, "rb") as inf:
        inb = inf.read()

    toVersion(inb, output, version)


if __name__ == "__main__":
    main()
