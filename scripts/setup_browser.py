"""Install the browser used by the service; system packages are opt-in."""
import argparse
import subprocess
import sys
from server import config  # Load the same .env as the service.
from server.browser import browser_launch_options


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--with-deps',action='store_true',help='also install Linux browser system packages (may require sudo)')
    args=parser.parse_args(argv)
    if browser_launch_options().get('channel')=='chrome':
        print('Browser: using installed Google Chrome.')
        return
    command=[sys.executable,'-m','playwright','install']
    if args.with_deps:command.append('--with-deps')
    subprocess.run(command+['chromium'],check=True)


if __name__=='__main__':main()
