# pybg: Backgroup
## What is this?

pybg is a tool that supports parallel job execution within shell scripts.

In Unix shell scripts, commands can be executed in parallel using background processes, but pybg simplifies job management with additional features.

Key Features:
- Management of job success and failure
- Handling of job output logs
- Limitation on the number of concurrently running jobs
- Automatic re-execution of failed jobs
- Support for job submission via Slurm

## Requirement

Windows is not supported

## Insatll

```sh
git clone
pip install ./pybg
```


## Usage


```sh
# 1. Clear all commands from the pool server
pybg clear group
# 2. Register jobs to the server
pybg add group echo Hello World
pybg add group sleep 40
pybg add group python -c "print('This is python')"
# 3. Dump commands in text and stop the pool server
pybg write group
# 4 Run commands
pybg run group
```


### Option

## Template generation


```sh
pybg template > run.sh
```


## Showing


```sh
```



### Slurm

### Internal behaviour
