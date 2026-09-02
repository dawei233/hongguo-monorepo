"""MP4 净化器：剔除 CDN 非标 box（saio/saiz/senc/...），重写 stco/co64 偏移
让 ExoPlayer 严格 demuxer 通过"""
import struct
import sys


def parse_box_header(buf, offset):
    """读取 box 头，返回 (type, data_offset, size, has_64bit)"""
    if offset + 8 > len(buf):
        return None
    size = struct.unpack('>I', buf[offset:offset + 4])[0]
    btype = buf[offset + 4:offset + 8].decode('latin-1', errors='replace')
    if size == 1:
        size = struct.unpack('>Q', buf[offset + 8:offset + 16])[0]
        data_offset = offset + 16
        has_64 = True
    elif size == 0:
        size = len(buf) - offset
        data_offset = offset + 8
        has_64 = False
    else:
        data_offset = offset + 8
        has_64 = False
    return {'type': btype, 'offset': offset, 'size': size, 'data_offset': data_offset, 'has_64': has_64}


def iter_boxes(buf, start, end):
    pos = start
    while pos + 8 <= end:
        b = parse_box_header(buf, pos)
        if not b or b['size'] <= 0:
            break
        yield b
        pos += b['size']


# 容器级要跳过的 box（在 stbl 内）
# saio/saiz/senc: CDN 字节伪加密标记
# sgpd/sbgp: Sample Group（ExoPlayer 在某些情况下会因此报 PARSING_CONTAINER_MALFORMED）
# stsl/sdpd/subs/stdp: Sub-sample/Partial Sample 等辅助 box（兼容性差）
SKIP_IN_STBL = {'saio', 'saiz', 'senc', 'sgpd', 'sbgp', 'stsl', 'sdpd', 'subs', 'stdp'}


def clean_stbl(buf, stbl_box):
    """清理 stbl：剔除 skip 类型，调整 stco/co64 偏移"""
    # 解析所有 box，分类处理
    preserved = []  # 保留的 box（已调整偏移）
    skipped = []

    delta = stbl_box.get('_delta', 0)  # 待调整的偏移差

    for b in iter_boxes(buf, stbl_box['data_offset'], stbl_box['offset'] + stbl_box['size']):
        raw = bytes(buf[b['offset']:b['offset'] + b['size']])

        if b['type'] in SKIP_IN_STBL:
            skipped.append(b['type'])
            continue

        if b['type'] == 'stco' and delta != 0:
            # 调整 stco 中的 32-bit 偏移
            payload = buf[b['data_offset']:b['offset'] + b['size']]
            count = struct.unpack('>I', payload[4:8])[0]
            new_payload = bytearray(payload[:8])
            for i in range(count):
                if 8 + 4 * (i + 1) > len(payload):
                    break
                old_off = struct.unpack('>I', payload[8 + 4 * i:12 + 4 * i])[0]
                new_off = max(0, old_off + delta)
                new_payload.extend(struct.pack('>I', new_off))
            preserved.append((b, bytes(new_payload)))
            continue

        if b['type'] == 'co64' and delta != 0:
            payload = buf[b['data_offset']:b['offset'] + b['size']]
            count = struct.unpack('>I', payload[4:8])[0]
            new_payload = bytearray(payload[:8])
            for i in range(count):
                if 8 + 8 * (i + 1) > len(payload):
                    break
                old_off = struct.unpack('>Q', payload[8 + 8 * i:16 + 8 * i])[0]
                new_off = max(0, old_off + delta)
                new_payload.extend(struct.pack('>Q', new_off))
            preserved.append((b, bytes(new_payload)))
            continue

        preserved.append((b, raw))

    if skipped:
        print(f"[sanitize]   stbl 剔除: {skipped}")

    # 重组 stbl 内容
    result = bytearray()
    for b, raw in preserved:
        # 用新的 size 重新打包头
        if b['type'] in ('stco', 'co64'):
            # 已处理过的数据
            if b['has_64']:
                # 原 64-bit
                result.extend(struct.pack('>I', 1))
                result.extend(b['type'].encode('latin-1'))
                result.extend(struct.pack('>Q', 8 + len(raw)))
                result.extend(raw)
            else:
                new_size = 8 + len(raw)
                if new_size > 0xFFFFFFFF:
                    result.extend(struct.pack('>I', 1))
                    result.extend(b['type'].encode('latin-1'))
                    result.extend(struct.pack('>Q', new_size + 8))
                    result.extend(raw)
                else:
                    result.extend(struct.pack('>I', new_size))
                    result.extend(b['type'].encode('latin-1'))
                    result.extend(raw)
        else:
            # 原样输出（保持原 size，因为内容不变）
            result.extend(raw)

    return result


def clean_container(buf, container_box, delta):
    """递归清理容器：处理 minf/stbl/stbl 内 box"""
    result = bytearray()
    for b in iter_boxes(buf, container_box['data_offset'], container_box['offset'] + container_box['size']):
        raw = bytes(buf[b['offset']:b['offset'] + b['size']])
        if b['type'] == 'stbl':
            # 在 stbl 内部递归清理（必须传 delta 给 clean_stbl 才能调整 stco/co64 偏移）
            b['_delta'] = delta
            new_stbl = clean_stbl(buf, b)
            # 包装为 stbl
            new_size = 8 + len(new_stbl)
            if new_size > 0xFFFFFFFF:
                result.extend(struct.pack('>I', 1))
                result.extend(b'stbl')
                result.extend(struct.pack('>Q', new_size + 8))
                result.extend(new_stbl)
            else:
                result.extend(struct.pack('>I', new_size))
                result.extend(b'stbl')
                result.extend(new_stbl)
            continue

        if b['type'] == 'stsd':
            # stsd 有特殊结构：version(1)+flags(3) + entry_count(4) + sample entries
            # sample entry (hvc1/hev1/avc1/mp4a) 本身是 box，但内部分布特殊
            # 直接原样保留（不要递归，里面是编码器配置，不能动）
            result.extend(raw)
            continue

        if b['type'] == 'minf':
            # 递归处理 minf
            new_minf = clean_container(buf, b, delta)
            new_size = 8 + len(new_minf)
            if new_size > 0xFFFFFFFF:
                result.extend(struct.pack('>I', 1))
                result.extend(b'minf')
                result.extend(struct.pack('>Q', new_size + 8))
                result.extend(new_minf)
            else:
                result.extend(struct.pack('>I', new_size))
                result.extend(b'minf')
                result.extend(new_minf)
            continue

        if b['type'] == 'mdia':
            new_mdia = clean_container(buf, b, delta)
            new_size = 8 + len(new_mdia)
            if new_size > 0xFFFFFFFF:
                result.extend(struct.pack('>I', 1))
                result.extend(b'mdia')
                result.extend(struct.pack('>Q', new_size + 8))
                result.extend(new_mdia)
            else:
                result.extend(struct.pack('>I', new_size))
                result.extend(b'mdia')
                result.extend(new_mdia)
            continue

        if b['type'] == 'trak':
            new_trak = clean_container(buf, b, delta)
            new_size = 8 + len(new_trak)
            if new_size > 0xFFFFFFFF:
                result.extend(struct.pack('>I', 1))
                result.extend(b'trak')
                result.extend(struct.pack('>Q', new_size + 8))
                result.extend(new_trak)
            else:
                result.extend(struct.pack('>I', new_size))
                result.extend(b'trak')
                result.extend(new_trak)
            continue

        if b['type'] == 'moov':
            new_moov = clean_container(buf, b, delta)
            new_size = 8 + len(new_moov)
            if new_size > 0xFFFFFFFF:
                result.extend(struct.pack('>I', 1))
                result.extend(b'moov')
                result.extend(struct.pack('>Q', new_size + 8))
                result.extend(new_moov)
            else:
                result.extend(struct.pack('>I', new_size))
                result.extend(b'moov')
                result.extend(new_moov)
            continue

        # 其它原样保留
        result.extend(raw)

    return result


def sanitize_mp4(input_path, output_path):
    """净化入口"""
    with open(input_path, 'rb') as f:
        data = bytearray(f.read())

    top_boxes = list(iter_boxes(data, 0, len(data)))
    print(f"[sanitize] 原文件 {len(data)} 字节，顶层 box: {[b['type'] for b in top_boxes]}")

    # 找 moov 和 mdat
    moov_box = next((b for b in top_boxes if b['type'] == 'moov'), None)
    mdat_boxes = [b for b in top_boxes if b['type'] == 'mdat']

    if not moov_box:
        print("[sanitize] 无 moov，跳过")
        return False

    # 计算 delta：旧 mdat 起始 vs 新 mdat 起始
    # 新文件结构：ftyp + 其他 + 新 moov + 所有 mdat
    # 我们要预估新 moov 大小（暂时按 1:1 估，不准）
    # 实际上没法先预估，所以先做一遍：
    # 步骤 1: 清理 moov 得到 new_moov 大小
    new_moov_content = clean_container(data, moov_box, 0)
    new_moov_size = 8 + len(new_moov_content)

    # 步骤 2: 计算新 mdat 偏移
    offset = 0
    for b in top_boxes:
        if b['type'] == 'ftyp':
            offset += b['size']
        elif b['type'] not in ('moov', 'mdat'):
            offset += b['size']
    # 现在 offset 是新 moov 之前的总长度
    new_moov_start = offset
    new_mdat_start = new_moov_start + new_moov_size
    old_mdat_start = mdat_boxes[0]['offset'] if mdat_boxes else 0
    delta = new_mdat_start - old_mdat_start

    print(f"[sanitize] 新 moov 大小: {new_moov_size} delta: {delta}")

    # 步骤 3: 实际重写 - 应用 delta 到 stco/co64
    new_moov_content_with_offset = clean_container(data, moov_box, delta)
    new_moov_size_actual = 8 + len(new_moov_content_with_offset)

    # 重新计算 delta（可能有微小差异）
    if new_moov_size_actual != new_moov_size:
        # 重做
        new_mdat_start = new_moov_start + new_moov_size_actual
        delta = new_mdat_start - old_mdat_start
        new_moov_content_with_offset = clean_container(data, moov_box, delta)
        new_moov_size_actual = 8 + len(new_moov_content_with_offset)

    # 写输出
    with open(output_path, 'wb') as out:
        # 写 ftyp
        ftyp = next((b for b in top_boxes if b['type'] == 'ftyp'), None)
        if ftyp:
            out.write(data[ftyp['offset']:ftyp['offset'] + ftyp['size']])

        # 写其他顶层
        for b in top_boxes:
            if b['type'] in ('ftyp', 'moov', 'mdat'):
                continue
            out.write(data[b['offset']:b['offset'] + b['size']])

        # 写新 moov（必须带 moov box header）
        new_moov_actual_size = 8 + len(new_moov_content_with_offset)
        if new_moov_actual_size > 0xFFFFFFFF:
            out.write(struct.pack('>I', 1))
            out.write(b'moov')
            out.write(struct.pack('>Q', new_moov_actual_size + 8))
        else:
            out.write(struct.pack('>I', new_moov_actual_size))
            out.write(b'moov')
        out.write(new_moov_content_with_offset)

        # 写所有 mdat
        for b in mdat_boxes:
            out.write(data[b['offset']:b['offset'] + b['size']])

    print(f"[sanitize] 输出: {output_path} ({new_moov_actual_size} 字节 moov)")
    return True


if __name__ == '__main__':
    if len(sys.argv) != 3:
        print("Usage: mp4_sanitize.py <input> <output>")
        sys.exit(1)
    ok = sanitize_mp4(sys.argv[1], sys.argv[2])
    sys.exit(0 if ok else 1)