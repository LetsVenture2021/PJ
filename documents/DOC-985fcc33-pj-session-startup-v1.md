# PJ Session Startup

> **Doc:** DOC-985fcc33 v1 · **Template:** sop · **Status:** DRAFT · **Created:** 2026-07-26T01:56:39.837900+00:00 · **Tags:** PJ,SOP,session startup,draft

## Objective

Provide a repeatable process for starting a PJ session from the project directory and sending an initial message.

## Scope

Applies to local PJ sessions launched from the command line.

## Roles

- **User:** Opens the terminal, starts the PJ environment, and submits the session message.

## Prerequisites

- The `pj` shell alias is configured.
- `~/.env` contains the required environment variables.
- The project virtual environment exists at `./venv`.
- `pj.py` is available in the current project directory.

## Procedure

1. Open a terminal and navigate to the PJ project directory.
2. Run the startup alias:
   ```bash
   pj
   ```
   The alias sources `~/.env` and activates the project virtual environment.
3. Start PJ and provide the initial message:
   ```bash
   ./venv/bin/python pj.py "<message>"
   ```
4. Confirm that PJ starts and processes the supplied message.

## Exceptions

- If `pj` is not recognized, verify that the alias is defined and that the shell configuration has been loaded.
- If environment variables are missing, confirm that `~/.env` exists and is readable.
- If the Python command fails, verify that `./venv/bin/python` and `pj.py` exist in the current directory.

## Revision Notes

- **Version 1:** Initial draft created July 26, 2026.
