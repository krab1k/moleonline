using System.IO.Compression;
using System.Net;
using System.Text;
using System.Text.Json.Nodes;
using Microsoft.AspNetCore.Mvc.Testing;
using Microsoft.Extensions.Configuration;

namespace Mole.API.Tests;

/// <summary>
/// The API in the test process, with its own working directory. MOLE is a shell script by default: the load (input in
/// folder 0) runs <c>load</c>, a computation runs <c>run</c>; both succeed at once unless told otherwise.
/// With <c>mole</c> set (e.g. the real MOLE), it is used instead.
/// The scripts see <c>$ALIVE</c>, a file that exists until the test ends: a run waiting for it (<see cref="UntilTestEnds"/>)
/// ends with the test, also when an assertion failed before the run was stopped.
/// </summary>
public sealed class Api : IDisposable
{
    public static readonly string Data = Path.Combine(AppContext.BaseDirectory, "data");
    public static readonly byte[] Structure = File.ReadAllBytes(Path.Combine(Data, "1tqn.cif"));

    private readonly string root = Directory.CreateTempSubdirectory("mole-api-tests-").FullName;
    private string Alive => Path.Combine(root, "alive");

    /// <summary>A script command running until the test ends</summary>
    public const string UntilTestEnds = "while [ -f \"$ALIVE\" ]; do sleep 0.1; done";
    private readonly WebApplicationFactory<Program> factory;

    public HttpClient Client { get; }
    public string WorkingDirectory => Path.Combine(root, "computations");

    public Api(string load = "exit 0", string run = "exit 0", int slots = 4, string? mole = null)
    {
        File.WriteAllText(Alive, "");
        mole ??= Script("mole", $"case \"$1\" in\n  */0/input.xml) {load} ;;\n  *) {run} ;;\nesac\n");
        var config = new Dictionary<string, string?>
        {
            ["Config:WorkingDirectory"] = WorkingDirectory,
            ["Config:MOLE"] = mole,
            ["Config:Pores"] = Script("pores", "exit 1"),
            ["Config:CSA"] = Path.Combine(AppContext.BaseDirectory, "Resources", "CSA_2_0_121113.txt"),
            ["Config:MaxConcurentComputations"] = slots.ToString(),
        };
        factory = new WebApplicationFactory<Program>().WithWebHostBuilder(b => b.ConfigureAppConfiguration((_, c) => c.AddInMemoryCollection(config)));
        Client = factory.CreateClient();
    }

    private string Script(string name, string body)
    {
        var path = Path.Combine(root, name);
        File.WriteAllText(path, $"#!/bin/sh\nALIVE='{Alive}'\n" + body);
        File.SetUnixFileMode(path, UnixFileMode.UserRead | UnixFileMode.UserWrite | UnixFileMode.UserExecute);
        return path;
    }

    public static byte[] Gzip(byte[] data)
    {
        using var output = new MemoryStream();
        using (var gzip = new GZipStream(output, CompressionLevel.Fastest)) gzip.Write(data);
        return output.ToArray();
    }

    public async Task<(HttpStatusCode Status, JsonNode Body)> UploadAsync(string fileName, byte[] content)
    {
        using var form = new MultipartFormDataContent { { new ByteArrayContent(content), "file", fileName } };
        return await ReadAsync(await Client.PostAsync("/Init", form));
    }

    /// <returns>Id of a computation whose structure is loaded</returns>
    public async Task<string> LoadAsync()
    {
        var (status, body) = await UploadAsync("1tqn.cif", Structure);
        Assert.Equal(HttpStatusCode.OK, status);
        var id = (string)body["ComputationId"]!;
        Assert.Equal("Initialized", (string)(await WaitAsync(id))["Status"]!);
        return id;
    }

    public async Task<(HttpStatusCode Status, JsonNode Body)> SubmitAsync(string id, string parameters = "{}") =>
        await ReadAsync(await Client.PostAsync($"/Submit/Mole/{id}", new StringContent(parameters, Encoding.UTF8, "application/json")));

    public async Task<(HttpStatusCode Status, JsonNode Body)> GetAsync(string path) => await ReadAsync(await Client.GetAsync(path));

    public async Task<(HttpStatusCode Status, JsonNode Body)> PostAsync(string path) => await ReadAsync(await Client.PostAsync(path, null));

    private static async Task<(HttpStatusCode, JsonNode)> ReadAsync(HttpResponseMessage response) =>
        (response.StatusCode, JsonNode.Parse(await response.Content.ReadAsStringAsync())!);

    /// <returns>The status once the load or the submission is no longer in progress</returns>
    public async Task<JsonNode> WaitAsync(string id, int submitId = 0, int seconds = 120)
    {
        for (var end = DateTime.UtcNow.AddSeconds(seconds); DateTime.UtcNow < end; await Task.Delay(100))
        {
            var (_, status) = await GetAsync(submitId == 0 ? $"/Status/{id}" : $"/Status/{id}?submitId={submitId}");
            if ((string)status["Status"]! is not ("Initializing" or "Running")) return status;
        }
        throw new TimeoutException($"Computation {id} is still in progress after {seconds} s.");
    }

    /// <summary>Waits until the fake MOLE started a computation, so that it is running now</summary>
    public async Task WaitForFileAsync(string path, int seconds = 10)
    {
        for (var end = DateTime.UtcNow.AddSeconds(seconds); DateTime.UtcNow < end; await Task.Delay(50))
            if (File.Exists(path)) return;
        throw new TimeoutException($"{path} did not appear.");
    }

    public void Dispose()
    {
        File.Delete(Alive); // the fake runs end
        Client.Dispose();
        factory.Dispose();
        try { Directory.Delete(root, recursive: true); }
        catch (IOException) { } // a run stopped by the end of the test may still be writing; it is a temporary directory
    }
}
