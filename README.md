# Code Inspector

[![Build Status](https://travis-ci.com/itsVale/code-inspector.svg?branch=master)](https://travis-ci.com/itsVale/code-inspector)
[![Updates](https://pyup.io/repos/github/itsVale/code-inspector/shield.svg)](https://pyup.io/repos/github/itsVale/code-inspector/)

Code Inspector is a bot that mainly provides tools intended to support programming
servers on [Discord](https://discordapp.com).

It requires Python 3.7+ and a PostgreSQL database and makes use of the discord.py rewrite branch.

You can host your own instance of the bot for development purposes but it is preferred to [invite
the bot](https://discordapp.com/oauth2/authorize?client_id=534029686301523987&scope=bot&permissions=124993)
to your guild instead of hosting your own instance of it. The code is hosted primarily for reference
and bugfixing.

## Setting up a development instance

There are various requirements you'll need to be able to set up an instance of the bot. This is intended
for development purposes if you're planning to commit to this repo.

- `git`, for acquiring the discord.py rewrite branch
- A PostgreSQL >=9.6 database to store relevant data
- A `config.yaml` file containing configuration data
- `libuv` to enable `uvloop`
- The Python bot requirements listed in `requirements.txt`

### git

#### Windows

Head over to [git-scm.com](https://git-scm.com/downloads) and get git for Windows.

#### Linux

`git` should be available from your system package manager.  
**Examples:**
```bash
# Debian-based distros
apt install git

# Arch-based distros
pacman -S git
```

#### macOS

`git` can be installed using the package manager [Homebrew](https://brew.sh/):
```bash
brew install git
```

### PostgreSQL

The installation varies based on the system:

#### Windows

PostgreSQL for Windows can be acquired from the [Windows installers](https://www.postgresql.org/download/windows/)
page.

After the installation, open the Start Menu and search for `SQL shell (psql)` and run it.

If you've changed any credentials (such as port) during the installation, type them in, otherwise just press Enter
until it asks for your password.

Enter the password you entered into the installer and psql should load into the postgres user.

#### Linux

The installation of PostgreSQL depends on your distro.

##### Arch:

Since Arch includes up to date PostgreSQL packages in their official repositories, simply run:
```bash
pacman -S postgresql
```

After installing, use `sudo -u postgres -i psql` to log into the postgres user.

##### Debian:

In order to get specific PostgreSQL versions on Debian, you will need to add an apt repository.

As apt requires root, you must be superuser for the following steps. If you are not already,
you can become superuser by typing `sudo su` into your terminal.

First, edit `/etc/apt/sources.list` or a subrule for it, e.g. `/etc/apt/sources.list.d/postgres.list`
to contain the following:
```bash
# Vary stretch-pgdg to jessie-pgdg, wheezy-pdgd, ... depending on your installation
deb http://apt.postgresql.org/pub/repos/apt/ stretch-pgdg main
```

Once this is done, you must add the PostgreSQL key to apt and update your local package list.
```bash
wget --quiet -O - https://www.postgresql.org/media/keys/ACCC4CF8.asc | apt-key add -
apt update
```

Finally, you can install PostgreSQL:
```bash
apt install postgresql-11
```

Now you can use `sudo -u postgres -i psql` to log in as the postgres user.

#### macOS

For macOS, we will use the [Homebrew package manager](https://brew.sh/).

You can install PostgreSQL using the following commands:
```bash
brew update
brew install postgresql
```

Then run `sudo -u postgres -i psql` to log in as the postgres user.

#### Setup

Now that you installed PostgreSQL on your system and entered the psql console, you need to setup
a database for the bot. First of all, you may want to create a new database user for the bot.
```bash
CREATE ROLE your_username LOGIN PASSWORD 'super secret password';
```

Then, you can create a database for your bot. You should also install the `pg_trgm` extension
in order to get all features of the bot working.
```bash
CREATE DATABASE code_inspector OWNER your_username;
CREATE EXTENSION pg_trgm;
```

Your PostgreSQL setup is now done. You can log out of psql by typing `\q`.

### config.yaml

The next step is to add configuration details to your bot over a `config.yaml` file.

In the repo, there's a file called `config.example.yaml`. Rename it to `config.yaml`
and fill out all details.

### libuv

uvloop is a library written in Cython that runs libuv under the hood. It can speed
up the bot's event loop up to 4 times. However, uvloop requires you to install libuv.

#### Windows

Not necessary. uvloop doesn't work on Windows no matter what. uvloop won't be used
in this case.

#### Linux

On Linux, you can install libuv using your package manager:
```bash
# Debian
apt install libuv0.10
# Arch
pacman -S libuv
```

#### macOS

On macOS, you can use Homebrew to install libuv with the following command:
```bash
brew install libuv
```

### requirements.txt

The last step is to install the bot dependencies.

First of all, create a venv for them:
```bash
python -m virtualenv venv

# On Windows:
"venv\Scripts\activate.bat"
# On Linux and macOS:
source venv/bin/activate
```

Then use pip to install the dependencies:
```bash
pip install -U -r requirements.txt
```

Once you are done, you can disable your venv using `deactivate`/`deactive.bat`.

### Running the bot

On the first start, it is necessary to create the database tables of the bot:
```bash
# Only necessary on the first bot start:
python launch.py db init all

# Add the --stream-log flag only if you want console logging output
python launch.py --stream-log
```