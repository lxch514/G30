#!/usr/bin/env python3
"""One explicit six-second RAM-to-G30 TCP capacity probe, never auto-started."""
import json,os,socket,time
from pathlib import Path
BASE=Path(__file__).resolve().parents[1]
result=dict(scope='Single TCP connection from board RAM to G30; no camera encoding, MediaCodec or recording in the transfer',port=18096)
with socket.socket() as server:
    server.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
    server.bind(('192.168.144.10',18096));server.listen(1);server.settimeout(20)
    connection,peer=server.accept()
    with connection:
        if peer[0]!='192.168.144.11':raise SystemExit('Probe only accepts the connected G30')
        connection.setsockopt(socket.IPPROTO_TCP,socket.TCP_NODELAY,1)
        connection.setsockopt(socket.SOL_SOCKET,socket.SO_SNDBUF,1024*1024)
        connection.settimeout(1)
        block=os.urandom(65536);sent=0;began=time.monotonic();error=''
        try:
            while time.monotonic()-began<6:connection.sendall(block);sent+=len(block)
            connection.shutdown(socket.SHUT_WR)
        except OSError as failure:error=str(failure)
        elapsed=time.monotonic()-began
        result.update(peer=peer[0],seconds=elapsed,complete_block_bytes_written=sent,write_mbps=sent*8/elapsed/1e6,error=error)
out=BASE/'run/validation/tcp-capacity-server.json';out.parent.mkdir(parents=True,exist_ok=True)
out.write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2),flush=True)
