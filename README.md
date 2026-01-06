# Common Crawl Response Linter

This project analyzes HTTP responses from the [Common Crawl](https://commoncrawl.org/) dataset using [httplint](https://github.com/mnot/httplint). It collects statistics on the types of issues found in the responses.

You can run this tool locally on your machine or on AWS EMR (Elastic MapReduce) for processing large amounts of data.

---

## 1. Local Setup & Running

Use this method to test the tool or process a small number of WARC files.

### Prerequisites
- Python 3.9 or higher installed.

### Step 1: Set up the environment
Open your terminal and run the following commands to create a virtual environment and install dependencies:

```bash
# Create a virtual environment named .venv
python3 -m venv .venv

# Activate the environment
# On macOS/Linux:
source .venv/bin/activate
# On Windows:
# .venv\Scripts\activate

# Install required libraries
pip install -r requirements.txt
```

### Step 2: Prepare a list of files to process
You need a text file containing the paths to Common Crawl WARC files. You can get these paths from Common Crawl's [index](https://commoncrawl.org/the-data/get-started/) or index files.

For testing, create a file named `sample_paths.txt` with one path:
```text
crawl-data/CC-MAIN-2024-51/segments/1733066035857.0/warc/CC-MAIN-20241201162023-20241201192023-00000.warc.gz
```

### Step 3: Run the linter locally
Run the CLI tool. This will download the WARC file via HTTP and process it.

```bash
# Process 1 file, limit to first 100 records for speed
python -m cc_lint.cli lint --paths-file sample_paths.txt --limit 1 --record-limit 100 --output stats.json
```

Output will be saved to `stats.json`.

### Step 4: Generate HTML Report
Turn the statistics into a readable HTML report with Redbot links for samples.

```bash
python -m cc_lint.cli report --input stats.json --output report.html
```

Open `report.html` in your browser to view the results.

---

## 2. AWS EMR (MapReduce) Setup & Running

Use this method to process hundreds or thousands of files using Amazon's cloud infrastructure.

**Note:** This involves costing money on your AWS account.

### Prerequisites (First Time Only)

1.  **Create an AWS Account**: If you don't have one, sign up at [aws.amazon.com](https://aws.amazon.com/).
2.  **Install AWS CLI**: Follow the [official instructions](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html) to install the `aws` command line tool.
3.  **Configure Credentials**:
    Run the configure command and enter your Access Key ID and Secret Access Key (you can generate these in the AWS Console under IAM -> Users -> Security credentials).
    
    ```bash
    aws configure
    # Region Name: us-east-1 (Recommended for Common Crawl)
    # Output Format: json
    ```

### Step 1: Run on EMR

The project is configured to make running on EMR easy using `mrjob`. This will automatically:
1.  Create a cluster of computers (EC2 instances).
2.  Install the necessary software on them.
3.  Run the linting job.
4.  Terminates the cluster when finished (saving you money).

Run the following command:

```bash
python -m cc_lint.mr -r emr --conf-path mrjob.conf --use-s3 sample_paths.txt
```

**Explanation of flags:**
-   `-r emr`: Tells the script to run on AWS EMR instead of locally.
-   `--conf-path mrjob.conf`: Uses our configuration to install dependencies on the cloud servers.
-   `--use-s3`: Tells the workers to fetch data directly from S3 (faster/cheaper within AWS) instead of over HTTP.
-   `sample_paths.txt`: The input file list. For a real run, this should be a larger list of paths.

### Monitoring & Output
-   The terminal will show the progress of the cluster (Starting, Bootstrap, Running, Terminating).
-   **Output**: The final statistics will be printed to your terminal (stdout). You can redirect this to a file:
    ```bash
    python -m cc_lint.mr ... > full_stats.json
    ```

### Important: Costs
-   This script uses `m5.xlarge` instances by default (configured in `mrjob.conf`).
-   Always check the [AWS EMR Console](https://console.aws.amazon.com/emr) after your job finishes to ensure the cluster has "Terminated" and you are no longer being billed. `mrjob` usually handles this, but it's good safety to check.
