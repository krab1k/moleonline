using Microsoft.AspNetCore.Mvc;
using System.Text.RegularExpressions;

namespace Mole.API.Controllers;

/// <summary>
/// Mole_api_IP_log.csv, next to the computations (it outlives the API): one line per load or submission request,
/// also a refused one: time (UTC, as in the status files), Init or Submit, "-", client address, PDB/Upload/Mole/Pores, computation id
/// ("-" when no computation was created).
/// </summary>
internal static partial class IpLog
{
    public const string FileName = "Mole_api_IP_log.csv";

    private static readonly Lock appends = new(); // concurrent appends to the same file fail

    [GeneratedRegex("[^A-Za-z0-9]")]
    private static partial Regex NotInId();

    public static void Write(ControllerBase controller, string workingDirectory, string request, string kind, string? computationId)
    {
        // ids are letters and digits; one from the request may be anything, one request stays one line
        var id = computationId == null ? "-" : NotInId().Replace(computationId.Length > 40 ? computationId[..40] : computationId, "?");
        var line = $"{DateTime.UtcNow:yyyy-MM-dd HH:mm:ss} {request} - {controller.HttpContext.Connection.RemoteIpAddress} {kind} {id}\n"; // read while the request is still alive
        var log = Path.Combine(workingDirectory, FileName);
        Task.Run(() => { lock (appends) File.AppendAllText(log, line); });
    }
}
