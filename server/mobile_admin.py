"""Run on the host Mac; approval endpoints are deliberately absent from the gateway."""
import argparse
import json
from pathlib import Path
from .mobile_access import AccessError
from .mobile_gateway import default_store


def main():
    parser=argparse.ArgumentParser(description='手机访问管理（在运行网关的电脑本地执行）')
    commands=parser.add_subparsers(dest='command',required=True)
    pair=commands.add_parser('pair');pair.add_argument('--url',required=True);pair.add_argument('--out',type=Path,required=True)
    pair.add_argument('--qr',action='store_true',help='额外输出 SVG 二维码，需要 requirements-mobile.lock')
    commands.add_parser('list')
    approve=commands.add_parser('approve');approve.add_argument('device_id');approve.add_argument('--allow-writes',action='store_true')
    revoke=commands.add_parser('revoke');revoke.add_argument('device_id')
    args=parser.parse_args();store=default_store()
    try:
        if args.command=='pair':
            qr_module=None
            if args.qr:
                try:
                    import qrcode
                    import qrcode.image.svg
                    qr_module=qrcode
                except ImportError:parser.error('先安装 requirements-mobile.lock，或省略 --qr 使用复制配对内容。')
            payload=store.issue_pairing(args.url)
            text=json.dumps(payload,ensure_ascii=False)
            args.out.parent.mkdir(parents=True,exist_ok=True)
            # Never overwrite another pairing artifact by accident.
            with args.out.open('x',encoding='utf-8') as file:
                args.out.chmod(0o600);file.write(text+'\n')
            if qr_module:
                dest=args.out.with_suffix('.svg')
                with dest.open('xb') as file:
                    dest.chmod(0o600);qr_module.make(text,image_factory=qr_module.image.svg.SvgPathImage).save(file)
            print('配对内容已写入文件；5 分钟有效、限用一次。扫码后执行 list，然后 approve 设备 ID。')
        elif args.command=='list':print(json.dumps(store.devices(),ensure_ascii=False,indent=2))
        elif args.command=='approve':
            store.approve(args.device_id,write=args.allow_writes);print('已批准手机：'+('查看和任务管理' if args.allow_writes else '仅查看'))
        elif args.command=='revoke':store.revoke(args.device_id);print('已撤销手机凭证。')
    except (AccessError,ValueError,FileExistsError) as exc:parser.exit(1,str(exc)+'\n')

if __name__=='__main__':main()
